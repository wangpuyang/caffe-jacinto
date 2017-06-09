from __future__ import print_function
import caffe
from google.protobuf import text_format
from models.model_libs import *
import models.jacintonet_v2

import math
import os
import shutil
import stat
import subprocess
import sys
import argparse

def get_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config_name', type=str, required=True, help='A configuration name')  
    parser.add_argument('--model_name', type=str, required=True, help='Model name')      
    parser.add_argument('--stage_name', type=str, required=True, help='Stage name')    
    parser.add_argument('--pretrain_model', type=str, default=None, help='Pretrained caffemodel name')       
    return parser.parse_args()
      
def main(): 
    args = get_arguments()
            
    ### Modify the following parameters accordingly ###
    # The directory which contains the caffe code.
    # We assume you are running the script at the CAFFE_ROOT.
    caffe_root = os.getcwd()

    # Set true if you want to start training right after generating all files.
    run_soon = True
    # Set true if you want to load from most recently saved snapshot.
    # Otherwise, we will load from the pretrain_model defined below.
    resume_training = True
    # If true, Remove old model files.
    remove_old_models = False

    resize_width = 224
    resize_height = 224

    train_data = "./data/ilsvrc12_train_lmdb" 
    test_data = "./data/ilsvrc12_val_lmdb"
    mean_value = 128 #used in a bias layer in the net.
    train_transform_param = {
            'mirror': True,
            'mean_value': [0, 0, 0],
            'crop_size': resize_width
            }
    test_transform_param = {
            'mirror': False,
            'mean_value': [0, 0, 0],
            'crop_size': resize_width
            }


        
    # If true, use batch norm for all newly added layers.
    # Currently only the non batch norm version has been tested.
    use_batchnorm = True
    # initial learning rate.
    base_lr = 0.1

    # Modify the job name if you want.
    base_name = "{}_{}".format(args.config_name,args.model_name)    
    job_name = "{}/{}".format(base_name,args.stage_name)
    
    # The name of the model. Modify it if you want.
    model_name = "{}".format(args.model_name)

    # Base dir
    base_dir = "training/{}".format(base_name)
    # Directory which stores the model .prototxt file.
    save_dir = "training/{}".format(job_name)
    # Directory which stores the snapshot of models.
    snapshot_dir = "training/{}".format(job_name)
    # Directory which stores the job script and log file.
    job_dir = "training/{}".format(job_name)
    # Directory which stores the detection results.
    output_result_dir = "training/{}".format(job_name)

    # model definition files.
    train_net_file = "{}/train.prototxt".format(save_dir)
    test_net_file = "{}/test.prototxt".format(save_dir)
    deploy_net_file = "{}/deploy.prototxt".format(save_dir)
    solver_file = "{}/solver.prototxt".format(save_dir)
    # snapshot prefix.
    snapshot_prefix = "{}/{}".format(snapshot_dir, model_name)
    # job script path.
    job_file_base = "{}/{}".format(job_dir, 'train')
    log_file = "{}.log".format(job_file_base)    
    job_file = "{}.sh".format(job_file_base)

    # Stores the test image names and sizes. Created by data/VOC0712/create_list.sh
    name_size_file = ""

    # The pretrained model. We use the Fully convolutional reduced (atrous) VGGNet.
    pretrain_model = args.pretrain_model

    # Stores LabelMapItem.
    label_map_file = ""


    # Solver parameters.
    # Defining which GPUs to use.
    gpus = "0,1" #gpus = "0"
    gpulist = gpus.split(",")
    num_gpus = len(gpulist)

    # Divide the mini-batch to different GPUs.
    batch_size = 128
    accum_batch_size = 256
    iter_size = accum_batch_size / batch_size
    solver_mode = P.Solver.CPU
    device_id = 0
    train_batch_size_in_proto = batch_size
    if num_gpus > 0:
      #batch_size_in_proto = int(math.ceil(float(batch_size) / num_gpus)) #not needed for nvcaffe
      iter_size = int(math.ceil(float(accum_batch_size) / (batch_size)))      
      solver_mode = P.Solver.GPU
      device_id = int(gpulist[0])

    # Which layers to freeze (no backward) during training.
    freeze_layers = []

    # Evaluate on whole test set.
    num_test_image = 50000
    test_batch_size = 50
    test_batch_size_in_proto = test_batch_size    
    test_iter = num_test_image / test_batch_size

    solver_param = {
        # Train parameters
        'base_lr': base_lr,
        'weight_decay': 0.0001,
        'lr_policy': "poly",
        'power': 1,
        #'lr_policy': "multistep",    
        #'stepvalue': 100000,
        'gamma': 0.1,
        'momentum': 0.9,
        'iter_size': iter_size,
        'max_iter': 320000,
        'snapshot': 10000,
        'display': 100,
        #'average_loss': 10,
        'type': "SGD",
        'solver_mode': solver_mode,
        #'device_id': device_id,
        'debug_info': False,
        'snapshot_after_train': True,
        # Test parameters
        'test_iter': [test_iter],
        'test_interval': 10000,
        'test_initialization': True,
        'random_seed': 33,
        }

    ### Hopefully you don't need to change the following ###
    # Check file.
    check_if_exist(train_data)
    check_if_exist(test_data)
    check_if_exist(label_map_file)
    if pretrain_model != None:    
      check_if_exist(pretrain_model)
    
    make_if_not_exist(base_dir)    
    make_if_not_exist(save_dir)
    make_if_not_exist(job_dir)
    make_if_not_exist(snapshot_dir)
      
    #----------------------
    #Net definition  
    def define_net(train):
        # Create train net.
        net = caffe.NetSpec()
          
        #if you want the train and test in same proto, 
        #get the proto string for the data layer in train phase seperately and return it
          
        train_proto_str = []
        if train==True:                 
          data_kwargs = { 'source': train_data, 'name': 'data', 'batch_size': train_batch_size_in_proto, 'backend': caffe_pb2.DataParameter.DB.Value('LMDB'), 'ntop':2 }          
          net['data'], net['label'] = L.Data(transform_param=train_transform_param, **data_kwargs)
          out_layer = 'data' 
        else:
          data_kwargs = { 'source': test_data, 'name': 'data', 'batch_size': test_batch_size_in_proto, 'backend': caffe_pb2.DataParameter.DB.Value('LMDB'), 'ntop':2 }        
          net['data'], net['label'] = L.Data(transform_param=test_transform_param,**data_kwargs)
          out_layer = 'data'
       
                
        bias_kwargs = { #fixed value with lr_mult=0
            'param': [dict(lr_mult=0, decay_mult=0)],
            'filler': dict(type='constant', value=(-mean_value)),
            }       
        net['data/bias'] = L.Bias(net[out_layer], in_place=False, **bias_kwargs)
        out_layer = 'data/bias'
                            
        if args.model_name == 'jacintonet11':
            out_layer = models.jacintonet_v2.jacintonet11(net, from_layer=out_layer, freeze_layers=freeze_layers)
        else:
            ValueError("Invalid model name")

        net["loss"] = L.SoftmaxWithLoss(net[out_layer], net['label'],
            propagate_down=[True, False])

        net["accuracy/top1"] = L.Accuracy(net[out_layer], net['label'],
            include=dict(phase=caffe_pb2.Phase.Value('TEST')))
        
        accuracy_param_top5 = { 'top_k': 5 }            
        net["accuracy/top5"] = L.Accuracy(net[out_layer], net['label'],
            accuracy_param=accuracy_param_top5, include=dict(phase=caffe_pb2.Phase.Value('TEST')))
                 
        return net
    #----------------------
              
    net = define_net(train=True)
    with open(train_net_file, 'w') as f:
        print('name: "{}_train"'.format(model_name), file=f)
        #if you want the train and test in same proto, 
        #get the proto string for the data layer, train phase.
        #print(train_proto_str, file=f) 
        print(net.to_proto(), file=f)
    if save_dir!=job_dir:        
      shutil.copy(train_net_file, job_dir)

    # Create test net.
    net = define_net(train=False)
    with open(test_net_file, 'w') as f:
        print('name: "{}_test"'.format(model_name), file=f)
        print(net.to_proto(), file=f)
    if save_dir!=job_dir:
      shutil.copy(test_net_file, job_dir)

    # Create deploy net.
    # Remove the first and last layer from test net.
    deploy_net = net
    with open(deploy_net_file, 'w') as f:
        net_param = deploy_net.to_proto()
        # Remove the few layers first
        del net_param.layer[0]
        del net_param.layer[-1]
        del net_param.layer[-2]    
        del net_param.layer[-3]          
        net_param.name = '{}_deploy'.format(model_name)
        net_param.input.extend(['data'])
        net_param.input_shape.extend([
            caffe_pb2.BlobShape(dim=[1, 3, resize_height, resize_width])])
        print(net_param, file=f)
    if save_dir!=job_dir:        
      shutil.copy(deploy_net_file, job_dir)

    # Create solver.
    solver = caffe_pb2.SolverParameter(
            train_net=train_net_file,
            test_net=[test_net_file],
            snapshot_prefix=snapshot_prefix,
            **solver_param)
            
    with open(solver_file, 'w') as f:
        print(solver, file=f)
    if save_dir!=job_dir:        
      shutil.copy(solver_file, job_dir)

    max_iter = 0
    # Find most recent snapshot.
    for file in os.listdir(snapshot_dir):
      if file.endswith(".solverstate"):
        basename = os.path.splitext(file)[0]
        iter = int(basename.split("{}_iter_".format(model_name))[1])
        if iter > max_iter:
          max_iter = iter

    train_src_param = None
    if pretrain_model != None:
      train_src_param = '--weights="{}" \\\n'.format(pretrain_model)
    if resume_training:
      if max_iter > 0:
        train_src_param = '--snapshot="{}_iter_{}.solverstate" \\\n'.format(snapshot_prefix, max_iter)

    if remove_old_models:
      # Remove any snapshots smaller than max_iter.
      for file in os.listdir(snapshot_dir):
        if file.endswith(".solverstate"):
          basename = os.path.splitext(file)[0]
          iter = int(basename.split("{}_iter_".format(model_name))[1])
          if max_iter > iter:
            os.remove("{}/{}".format(snapshot_dir, file))
        if file.endswith(".caffemodel"):
          basename = os.path.splitext(file)[0]
          iter = int(basename.split("{}_iter_".format(model_name))[1])
          if max_iter > iter:
            os.remove("{}/{}".format(snapshot_dir, file))

    # Create job file.
    with open(job_file, 'w') as f:
      f.write('cd {}\n'.format(caffe_root))
      f.write('../../build/tools/caffe train \\\n')
      f.write('--solver="{}" \\\n'.format(solver_file))
      if train_src_param != None:
        f.write(train_src_param)
      if solver_param['solver_mode'] == P.Solver.GPU:
        f.write('--gpu "{}" 2>&1 | tee {}\n'.format(gpus, log_file))
      else:
        f.write('2>&1 | tee {}\n'.format(log_file))

    # Copy the python script to job_dir.
    py_file = os.path.abspath(__file__)
    shutil.copy(py_file, job_dir)

    # Run the job.
    os.chmod(job_file, stat.S_IRWXU)
    if run_soon:
      subprocess.call(job_file, shell=True)
  
  
if __name__ == "__main__":
  main()  
