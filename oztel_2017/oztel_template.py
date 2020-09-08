# Script based on template.py

##########################
#   ARGS COMPROBATION    #
##########################

import argparse
parser = argparse.ArgumentParser(
    description="Template based of template/template.py")
parser.add_argument("base_work_dir",
                    help="Path to code base dir , i.e ~/DeepLearning_EM")
parser.add_argument("data_dir", help="Path to data base dir")
parser.add_argument("result_dir",
                    help="Path to where the resulting output of the job will "\
                    "be stored")
parser.add_argument("-id", "--job_id", "--id", help="Job identifier", 
                    default="unknown_job")
parser.add_argument("-rid","--run_id", "--rid", help="Run number of the same job", 
                    type=int, default=0)
parser.add_argument("-gpu","--gpu", dest="gpu_selected", 
                    help="GPU number according to 'nvidia-smi' command",
                    required=True)
args = parser.parse_args()


##########################
#        PREAMBLE        #
##########################

import os
import sys
sys.path.insert(0, args.base_work_dir)

# Working dir
os.chdir(args.base_work_dir)

# Limit the number of threads
from util import limit_threads, set_seed, create_plots, store_history,\
                 TimeHistory, threshold_plots, save_img, \
                 calculate_2D_volume_prob_map, divide_images_on_classes, \
                 save_filters_of_convlayer
limit_threads()

# Try to generate the results as reproducible as possible
seed_value = 42                                                                 
set_seed(42)

crops_made = False
job_identifier = args.job_id + '_' + str(args.run_id)


##########################
#        IMPORTS         #
##########################

import datetime
import random
import numpy as np
import math
import time
import tensorflow as tf
from data_manipulation import load_and_prepare_2D_data, crop_data,\
                              merge_data_without_overlap,\
                              crop_data_with_overlap, merge_data_with_overlap, \
                              check_binary_masks, img_to_onehot_encoding,\
                              load_data_from_dir
from custom_da_gen import ImageDataGenerator
from keras_da_gen import keras_da_generator, keras_gen_samples
from oztel_2017.cnn_oztel import cnn_oztel, cnn_oztel_test
from metrics import jaccard_index, jaccard_index_numpy, voc_calculation,\
                    DET_calculation
from tensorflow.keras.callbacks import EarlyStopping
from tensorflow.keras.models import load_model
from PIL import Image
from tqdm import tqdm
from smooth_tiled_predictions import predict_img_with_smooth_windowing, \
                                     predict_img_with_overlap,\
                                     ensemble8_2d_predictions
from tensorflow.keras.utils import plot_model
from callbacks import ModelCheckpoint
from post_processing import spuriuous_detection_filter, calculate_z_filtering,\
                            boundary_refinement_watershed2
import shutil                                                                   
from tensorflow.keras.preprocessing.image import ImageDataGenerator as kerasDA  
from sklearn.metrics import classification_report, confusion_matrix


############
#  CHECKS  #
############

now = datetime.datetime.now()
print("Date : {}".format(now.strftime("%Y-%m-%d %H:%M:%S")))
print("Arguments: {}".format(args))
print("Python       : {}".format(sys.version.split('\n')[0]))
print("Numpy        : {}".format(np.__version__))
print("Keras        : {}".format(tf.keras.__version__))
print("Tensorflow   : {}".format(tf.__version__))
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID";
os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu_selected;


##########################                                                      
#  EXPERIMENT VARIABLES  #
##########################

### Dataset variables
# Main dataset data/mask paths
train_path = os.path.join(args.data_dir, 'train', 'x')
train_mask_path = os.path.join(args.data_dir, 'train', 'y')
test_path = os.path.join(args.data_dir, 'test', 'x')
test_mask_path = os.path.join(args.data_dir, 'test', 'y')
# Percentage of the training data used as validation                            
perc_used_as_val = 0.2
# Create the validation data with random images of the training data. If False
# the validation data will be the last portion of training images.
random_val_data = True


### Dataset shape
# Note: train and test dimensions must be the same when training the network and
# making the predictions. Be sure to take care of this if you are not going to
# use "crop_data()" with the arg force_shape, as this function resolves the 
# problem creating always crops of the same dimension
img_train_shape = (1024, 768, 1)
img_test_shape = (1024, 768, 1)


### Extra datasets variables
# Paths, shapes and discard values for the extra dataset used together with the
# main train dataset, provided by train_path and train_mask_path variables, to 
# train the network with. If the shape of the datasets differ the best option
# To normalize them is to make crops ("make_crops" variable)
extra_datasets_data_list = []
extra_datasets_mask_list = []
extra_datasets_data_dim_list = []
extra_datasets_discard = []
### Example of use:
# Path to the data:
# extra_datasets_data_list.append(os.path.join('kasthuri_pp', 'reshaped_fibsem', 'train', 'x'))
# Path to the mask: 
# extra_datasets_mask_list.append(os.path.join('kasthuri_pp', 'reshaped_fibsem', 'train', 'y'))
# Shape of the images:
# extra_datasets_data_dim_list.append((877, 967, 1))
# Discard value to apply in the dataset (see "Discard variables" for more details):
# extra_datasets_discard.append(0.05)                                             
#
# Number of crop to take form each dataset to train the network. If 0, the      
# variable will be ignored                                                      
num_crops_per_dataset = 0


### Crop variables
# Shape of the crops
crop_shape = (32, 32, 1)
# To make crops on the train data
make_crops = True
# To check the crops. Useful to ensure that the crops have been made 
# correctly. Note: if "discard_cropped_images" is True only the run that 
# prepare the discarded data will check the crops, as the future runs only load 
# the crops stored by this first run
check_crop = True 
# Instead of make the crops before the network training, this flag activates
# the option to extract a random crop of each train image during data 
# augmentation (with a crop shape defined by "crop_shape" variable). This flag
# is not compatible with "make_crops" variable
random_crops_in_DA = False 
test_ov_crops = 16 # Only active with random_crops_in_DA
probability_map = False # Only active with random_crops_in_DA                       
w_foreground = 0.94 # Only active with probability_map
w_background = 0.06 # Only active with probability_map


### Normalization
# To normalize the data dividing by the mean pixel value
normalize_data = False                                                          
# Force the normalization value to the given number instead of the mean pixel 
# value
norm_value_forced = -1                                                          


### Data augmentation (DA) variables
# To decide which type of DA implementation will be used. Select False to 
# use Keras API provided DA, otherwise, a custom implementation will be used
custom_da = False
# Create samples of the DA made. Useful to check the output images made. 
# This option is available for both Keras and custom DA
aug_examples = True 
# To shuffle the training data on every epoch:
# (Best options: Keras->False, Custom->True)
shuffle_train_data_each_epoch = custom_da
# To shuffle the validation data on every epoch:
# (Best option: False in both cases)
shuffle_val_data_each_epoch = False

### Options available for Keras Data Augmentation
# Make a bit of zoom in the images
k_zoom = False 
# widtk_h_shift_range (more details in Keras ImageDataGenerator class)
k_w_shift_r = 0.0
# height_shift_range (more details in Keras ImageDataGenerator class)
k_h_shift_r = 0.0
# k_shear_range (more details in Keras ImageDataGenerator class)
k_shear_range = 0.0 
# Range to pick a brightness value from to apply in the images. Available in 
# Keras. Example of use: k_brightness_range = [1.0, 1.0]
k_brightness_range = None 

### Options available for Custom Data Augmentation 
# Histogram equalization
hist_eq = False  
# Elastic transformations
elastic = False
# Median blur                                                             
median_blur = False
# Gaussian blur
g_blur = False                                                                  
# Gamma contrast
gamma_contrast = False      

### Options available for both, Custom and Kera Data Augmentation
# Rotation of 90, 180 or 270
rotation90 = False
# Range of rotation. Set to 0 to disable it
rotation_range = 180
# To make vertical flips 
vflips = True
# To make horizontal flips
hflips = True


### Load previously generated model weigths
# To activate the load of a previous training weigths instead of train 
# the network again
load_previous_weights = True
load_previous_weights_ft = True
# ID of the previous experiment to load the weigths from 
previous_job_weights = args.job_id
# To activate the fine tunning
fine_tunning = False
# ID of the previous weigths to load the weigths from to make the fine tunning 
fine_tunning_weigths = args.job_id
# Prefix of the files where the weights are stored/loaded from
weight_files_prefix = 'model.fibsem_'
# Wheter to find the best learning rate plot. If this options is selected the
# training will stop when 5 epochs are done
use_LRFinder = False


### Experiment main parameters
# Loss type, three options: "bce" or "w_bce_dice", which refers to binary cross 
# entropy (BCE) and BCE and Dice with with a weight term on each one (that must 
# sum 1) to calculate the total loss value. NOTE: "w_bce" is not implemented on 
# this template type: please use big_data_template.py instead.
loss_type = "bce"
# Batch size value
batch_size_value = 32
# Optimizer to use. Possible values: "sgd" or "adam"
optimizer = "adam"
# Learning rate used by the optimization method
learning_rate_value = 0.0001
# Number of epochs to train the network
epochs_value = 360
# Number of epochs to stop the training process after no improvement
patience = epochs_value
# If weights on data are going to be applied. To true when loss_type is 'w_bce' 
weights_on_data = True if loss_type == "w_bce" else False


### Network architecture specific parameters
# Number of feature maps on each level of the network. It's dimension must be 
# equal depth+1.
feature_maps = [32, 64, 128, 256, 512]
# Depth of the network
depth = 4
# To activate the Spatial Dropout instead of use the "normal" dropout layer
spatial_dropout = False
# Values to make the dropout with. It's dimension must be equal depth+1. Set to
# 0 to prevent dropout 
dropout_values = [0.1, 0.1, 0.2, 0.2, 0.3]
# To active batch normalization
batch_normalization = False
# Kernel type to use on convolution layers
kernel_init = 'he_normal'
# Activation function to use                                                    
activation = "relu" 
# Active flag if softmax or one channel per class is used as the last layer of
# the network. Custom DA needed.
softmax_out = True


### DET metric variables
# More info of the metric at http://celltrackingchallenge.net/evaluation-methodology/ 
# and https://public.celltrackingchallenge.net/documents/Evaluation%20software.pdf
# NEEDED CODE REFACTORING OF THIS VARIABLE
det_eval_ge_path = os.path.join(args.result_dir, "..", 'cell_challenge_eval',
                                 'gen_' + job_identifier)
# Path where the evaluation of the metric will be done
det_eval_path = os.path.join(args.result_dir, "..", 'cell_challenge_eval', 
                             args.job_id, job_identifier)
# Path where the evaluation of the metric for the post processing methods will 
# be done
det_eval_post_path = os.path.join(args.result_dir, "..", 'cell_challenge_eval', 
                                  args.job_id, job_identifier + '_s')
# Path were the binaries of the DET metric is stored
det_bin = os.path.join(args.base_work_dir, 'cell_cha_eval' ,'Linux', 'DETMeasure')
# Number of digits used for encoding temporal indices of the DET metric
n_dig = "3"


### Paths of the results                                             
result_dir = os.path.join(args.result_dir, 'results', job_identifier)

# Directory where binarized predicted images will be stored
result_bin_dir_per_image = os.path.join(result_dir, 'per_image_binarized')
# Directory where predicted images will be stored
result_no_bin_dir_per_image = os.path.join(result_dir, 'per_image_no_binarized')
# Folder where the smoothed images will be stored
smo_bin_dir_per_image = os.path.join(result_dir, 'per_image_smooth')
# Folder where the smoothed images (no binarized) will be stored
smo_no_bin_dir_per_image = os.path.join(result_dir, 'per_image_smooth_no_bin')
# Folder where the images with the z-filter applied will be stored
zfil_dir_per_image = os.path.join(result_dir, 'per_image_zfil')
# Folder where the images with smoothing and z-filter applied will be stored
smo_zfil_dir_per_image = os.path.join(result_dir, 'per_image_smo_zfil')

# Directory where binarized predicted images with 50% of overlap will be stored
result_bin_dir_50ov = os.path.join(result_dir, '50ov_binarized')
# Directory where predicted images with 50% of overlap will be stored
result_no_bin_dir_50ov = os.path.join(result_dir, '50ov_no_binarized')
# Folder where the images with the z-filter applied will be stored
zfil_dir_50ov = os.path.join(result_dir, '50ov_zfil')

# Directory where binarired predicted images obtained from feeding the full
# image will be stored
result_bin_dir_full = os.path.join(result_dir, 'full_binarized')
# Directory where predicted images obtained from feeding the full image will
# be stored
result_no_bin_dir_full = os.path.join(result_dir, 'full_no_binarized')
# Folder where the smoothed images will be stored
smo_bin_dir_full = os.path.join(result_dir, 'full_8ensemble')
# Folder where the smoothed images (no binarized) will be stored
smo_no_bin_dir_full = os.path.join(result_dir, 'full_8ensemble')
# Folder where the images with the z-filter applied will be stored
zfil_dir_full = os.path.join(result_dir, 'full_zfil')
# Folder where the images passed through the spurious detection filtering will
# be saved in
spu_dir_full = os.path.join(result_dir, 'full_spu')
# Folder where watershed debugging images will be placed in
wa_debug_dir_full = os.path.join(result_dir, 'full_watershed_debug')
# Folder where watershed output images will be placed in
wa_dir_full = os.path.join(result_dir, 'full_watershed')
# Folder where spurious detection + watershed + z-filter images' watershed
# markers will be placed in
spu_wa_zfil_wa_debug_dir = os.path.join(result_dir, 'full_wa_spu_zfil_wa_debug')
# Folder where spurious detection + watershed + z-filter images will be placed in
spu_wa_zfil_dir_full = os.path.join(result_dir, 'full_wa_spu_zfil')

# Name of the folder where the charts of the loss and metrics values while 
# training the network will be shown. This folder will be created under the
# folder pointed by "args.base_work_dir" variable 
char_dir = os.path.join(result_dir, 'charts')
# Directory where weight maps will be stored                                    
loss_weight_dir = os.path.join(result_dir, 'loss_weights', args.job_id)
# Folder where smaples of DA will be stored
da_samples_dir = os.path.join(result_dir, 'aug')
# Folder where crop samples will be stored
check_crop_path = os.path.join(result_dir, 'check_crop')
# Name of the folder where weights files will be stored/loaded from. This folder
# must be located inside the directory pointed by "args.base_work_dir" variable.
# If there is no such directory, it will be created for the first time
h5_dir = os.path.join(args.result_dir, 'h5_files')
# Name of the folder to store the probability map to avoid recalculating it on
# every run
prob_map_dir = os.path.join(args.result_dir, 'prob_map')
# Folder where LRFinder callback will store its plot
lrfinder_dir = os.path.join(result_dir, 'LRFinder')


### Callbacks
# To measure the time
time_callback = TimeHistory()
# Stop early and restore the best model weights when finished the training
earlystopper = EarlyStopping(
    patience=patience, verbose=1, restore_best_weights=True)
# Save the best model into a h5 file in case one need again the weights learned
os.makedirs(h5_dir, exist_ok=True)
checkpointer = ModelCheckpoint(
    os.path.join(h5_dir, weight_files_prefix + job_identifier + '.h5'),
    verbose=1, save_best_only=True)
# Check the best learning rate using the code from:
#  https://github.com/WittmannF/LRFinder
if use_LRFinder:
    lr_finder = LRFinder(min_lr=10e-9, max_lr=10e-3, lrfinder_dir=lrfinder_dir)
    os.makedirs(lrfinder_dir, exist_ok=True)


print("###################\n"
      "#  SANITY CHECKS  #\n"
      "###################\n")

check_binary_masks(train_mask_path)
check_binary_masks(test_mask_path)
if extra_datasets_mask_list: 
    for i in range(len(extra_datasets_mask_list)):
        check_binary_masks(extra_datasets_mask_list[i])

#if softmax_out and not custom_da:
#    raise ValuError("'custom_da' needed when 'softmax_out' is active")


print("##################################\n"                                    
      "#  OZTEL TRAIN DATA PREPARATION  #\n"                                    
      "##################################\n")                                   
                                                                                
# Train directories                                                             
p_train = os.path.join(args.result_dir, "prep_data", "train")                   
p_train_cls = os.path.join(p_train, "classification")                           
p_train_ss = os.path.join(p_train, "semantic_seg")                              
p_train_ss_x = os.path.join(p_train_ss, "x")                                    
p_train_ss_y = os.path.join(p_train_ss, "y")                                    
                                                                                
# Validation directories                                                        
p_val = os.path.join(args.result_dir, "prep_data", "val")                       
p_val_ss = os.path.join(p_val, "semantic_seg")                                  
p_val_ss_x = os.path.join(p_val_ss, "x")                                        
p_val_ss_y = os.path.join(p_val_ss, "y")                                        
                                                                                
# Variable to control the number of background samples to add into the training 
# data. This is done because as the problem is not balanced, maybe one decide to
# drop some background data. Set a high value (like 100) to ensure all background
# images will be used                                                           
train_B_samples_times_M = 100                                                   
                                                                                
# To create more mitocondria class images.                                      
# Total images = mitochondria_class_images + (mul_mito*mitochondria_class_images)
mul_mito = 2                                              
if not os.path.exists(p_train):                                            
                                                                                
    print("#################################\n"                                 
          "#  Divide the data into clases  #\n"                                 
          "#################################\n")                                
                                                                                
    X_train = load_data_from_dir(                                               
        train_path, (img_train_shape[1], img_train_shape[0], img_train_shape[2]))
    Y_train = load_data_from_dir(                                               
        train_mask_path, (img_train_shape[1], img_train_shape[0],               
        img_train_shape[2]))                                                    
    print("*** Loaded train data shape is: {}".format(X_train.shape))           
                                                                                
    X_train, Y_train, _ = crop_data(X_train, crop_shape, data_mask=Y_train)     
    divide_images_on_classes(X_train, Y_train/255, p_train, th=0.8)             
                                                                                
    # Path were divide_images_on_classes stored the data                        
    p_train_x_b = os.path.join(p_train, "x", "class0")                          
    p_train_x_m = os.path.join(p_train, "x", "class1")                          
    p_train_y_b = os.path.join(p_train, "y", "class0")                          
    p_train_y_m = os.path.join(p_train, "y", "class1")                          
                                                                                
                                                                                
                                                                                
    print("################################\n"                                  
          "#  Create the validation data  #\n"                                  
          "################################\n")                                 
    if not os.path.exists(p_val):
        print("Creating validation data . . .")                                 
        p_val_x_b = os.path.join(p_val, "x", "class0")                          
        p_val_x_m = os.path.join(p_val, "x", "class1")                          
        p_val_y_b = os.path.join(p_val, "y", "class0")                          
        p_val_y_m = os.path.join(p_val, "y", "class1")                          
        os.makedirs(p_val_x_b, exist_ok=True)                                   
        os.makedirs(p_val_x_m, exist_ok=True)                                   
        os.makedirs(p_val_y_b, exist_ok=True)                                   
        os.makedirs(p_val_y_m, exist_ok=True)                                   
                                                                                
        # Choose randomly selected images from train to generate the validation 
        c1_samples = len(next(os.walk(p_train_x_m))[2])                         
        c1_num_val_samples = int(c1_samples*perc_used_as_val)                   
        c0_samples = len(next(os.walk(p_train_x_b))[2])                         
        c0_num_val_samples = int(c0_samples*perc_used_as_val)                   
        f = random.sample(os.listdir(p_train_x_b), c0_num_val_samples)          
        for i in tqdm(range(c0_num_val_samples)):                               
            shutil.move(os.path.join(p_train_x_b, f[i]),                        
                        os.path.join(p_val_x_b, f[i]))                          
            shutil.move(os.path.join(p_train_y_b, f[i].replace("im", "mask")),  
                        os.path.join(p_val_y_b, f[i].replace("im", "mask")))    
                                                                                
        f = random.sample(os.listdir(p_train_x_m), c1_num_val_samples)          
        for i in tqdm(range(c1_num_val_samples)):                               
            shutil.move(os.path.join(p_train_x_m, f[i]),                        
                        os.path.join(p_val_x_m, f[i]))                          
            shutil.move(os.path.join(p_train_y_m, f[i].replace("im", "mask")),  
                        os.path.join(p_val_y_m, f[i].replace("im", "mask")))    
                                                                                
        # Create directory for semantic segmentation and copy the data there    
        os.makedirs(p_val_ss_x, exist_ok=True)                                  
        os.makedirs(p_val_ss_y, exist_ok=True)                                  
        for item in os.listdir(p_val_x_b):                                      
            shutil.copy2(os.path.join(p_val_x_b, item), p_val_ss_x)             
        for item in os.listdir(p_val_x_m):                                      
            shutil.copy2(os.path.join(p_val_x_m, item), p_val_ss_x)             
        for item in os.listdir(p_val_y_b):                                      
            shutil.copy2(os.path.join(p_val_y_b, item), p_val_ss_y)             
        for item in os.listdir(p_val_y_m):                                      
            shutil.copy2(os.path.join(p_val_y_m, item), p_val_ss_y)             
                                                                                
    del X_train, Y_train                                                        
                                                                                
                                                                                
    print("############################################################\n"      
          "#  Balance the classes to have the same amount of samples  #\n"      
          "############################################################\n")     
                                                                                
    p_train_e_x = os.path.join(p_train, "x", "class1-extra")                    
    p_train_e_y = os.path.join(p_train, "y", "class1-extra")                    
                                                                                
    # Load mitochondria class labeled samples                                   
    mito_data = load_data_from_dir(p_train_x_m, crop_shape)                     
    mito_mask_data = load_data_from_dir(p_train_y_m, crop_shape)                
                                                                                
    background_ids = len(next(os.walk(p_train_x_b))[2])                         
    num_samples_extra = mul_mito*mito_data.shape[0]                             
                                                                                
    # Create a generator                                                        
    mito_gen_args = dict(                                                       
        X=mito_data, Y=mito_mask_data, batch_size=batch_size_value,             
        shape=(crop_shape[0],crop_shape[1],1), shuffle=False, da=False,
        rotation_range=0)                                                       
    mito_generator = ImageDataGenerator(**mito_gen_args)                        
                                                                                
    # Create the new samples                                                    
    extra_x, extra_y = mito_generator.get_transformed_samples(num_samples_extra)
    save_img(X=extra_x, data_dir=p_train_e_x, Y=extra_y, mask_dir=p_train_e_y,  
             prefix="e")                                                        
                                                                                
    print("####################################################\n"              
          "#  Create train directory tree for classification  #\n"              
          "####################################################\n")             
                                                                                
    p_train_cls_b = os.path.join(p_train_cls, "class0")                         
    p_train_cls_m = os.path.join(p_train_cls, "class1")                         
    print("Gathering all train samples into one folder . . .")                  
    shutil.copytree(p_train_x_m, p_train_cls_m)                                 
    for item in tqdm(os.listdir(p_train_e_x)):                                  
        shutil.copy2(os.path.join(p_train_e_x, item), p_train_cls_m)            
                                                                                
    # Take the same amount of background and mitochondria samples               
    os.makedirs(p_train_cls_b, exist_ok=True)                                   
    c1_samples = len(next(os.walk(p_train_cls_m))[2])                           
    c0_samples = len(next(os.walk(p_train_x_b))[2])                             
    if c0_samples < c1_samples*train_B_samples_times_M:                         
        total_samples = c0_samples                                              
    else:                                                                       
        total_samples = c1_samples*train_B_samples_times_M                      
    f = random.sample(os.listdir(p_train_x_b), total_samples)                   
    for i in tqdm(range(total_samples)):                                        
        shutil.copy2(os.path.join(p_train_x_b, f[i]),                           
                     os.path.join(p_train_cls_b, f[i]))                         
                                                                                
    print("###########################################################\n"       
          "#  Create train directory tree for semantic segmentation  #\n"       
          "###########################################################\n")      
                                                                                
    if not os.path.exists(p_train_ss):                                     
        os.makedirs(p_train_ss_x, exist_ok=True)                                
        os.makedirs(p_train_ss_y, exist_ok=True)                                
        for item in os.listdir(p_train_x_b):                                    
            shutil.copy2(os.path.join(p_train_x_b, item), p_train_ss_x)         
        for item in os.listdir(p_train_x_m):                                    
            shutil.copy2(os.path.join(p_train_x_m, item), p_train_ss_x)         
        for item in os.listdir(p_train_e_x):                                    
            shutil.copy2(os.path.join(p_train_e_x, item), p_train_ss_x)         
        for item in os.listdir(p_train_y_b):                                    
            shutil.copy2(os.path.join(p_train_y_b, item), p_train_ss_y)         
        for item in os.listdir(p_train_y_m):                                    
            shutil.copy2(os.path.join(p_train_y_m, item), p_train_ss_y)         
        for item in os.listdir(p_train_e_y):                                    
            shutil.copy2(os.path.join(p_train_e_y, item), p_train_ss_y)         
                                                                                
    del mito_data, mito_mask_data                                               
                                                                                
orig_test_shape = img_test_shape                                                
                                                                                
# Finally load test data                                                        
X_test = load_data_from_dir(                                                    
    test_path, (img_test_shape[1], img_test_shape[0], img_test_shape[2]))       
Y_test = load_data_from_dir(                                                    
    test_mask_path, (img_test_shape[1], img_test_shape[0], img_test_shape[2]))  
print("*** Loaded test data shape is: {}".format(X_test.shape))                 


print("###########################\n"
      "#  EXTRA DATA GENERATION  #\n"
      "###########################\n")

# Train generator based on the new prepared training data on the last section   
datagen = kerasDA(rescale=1./255, rotation_range=180)                           
train_generator = datagen.flow_from_directory(                                  
    p_train_cls, target_size=crop_shape[:2], class_mode="binary",               
    color_mode="grayscale", batch_size=batch_size_value,                        
    shuffle=shuffle_train_data_each_epoch, seed=seed_value)                     
                                                                                
# Validation generator based on the validation images previously prepared       
datagen = kerasDA(rescale=1./255)                                               
val_generator = datagen.flow_from_directory(                                    
    os.path.join(p_val, "x"), target_size=crop_shape[:2], class_mode="binary",  
    color_mode="grayscale", batch_size=batch_size_value,                        
    shuffle=shuffle_val_data_each_epoch, seed=seed_value)                       
                                                                                
# Test generator based on X_test and Y_test                                     
data_gen_test_args = dict(                                                      
    X=X_test, Y=Y_test, batch_size=batch_size_value,                            
    shape=(img_test_shape[1],img_test_shape[0],1), shuffle=False,    
    da=False, softmax_out=softmax_out)                                          
test_generator = ImageDataGenerator(**data_gen_test_args)                       


print("#################################\n"
      "#  BUILD AND TRAIN THE NETWORK  #\n"
      "#################################\n")

print("Creating the network . . .")
model = cnn_oztel(crop_shape, lr=learning_rate_value, activation=activation, 
                  optimizer=optimizer)      

# Check the network created
model.summary(line_length=150)
os.makedirs(char_dir, exist_ok=True)
model_name = os.path.join(char_dir, "model_plot_" + job_identifier + ".png")
#plot_model(model, to_file=model_name, show_shapes=True, show_layer_names=True)

if not load_previous_weights:
    if fine_tunning:                                                    
        h5_file=os.path.join(h5_dir, weight_files_prefix + fine_tunning_weigths 
                             + '_' + args.run_id + '.h5')     
        print("Fine-tunning: loading model weights from h5_file: {}"
              .format(h5_file))
        model.load_weights(h5_file)                                             
   
    if use_LRFinder:
        print("Training just for 10 epochs . . .")
        results = model.fit(x=train_generator, validation_data=val_generator,
            validation_steps=math.ceil(X_val.shape[0]/batch_size_value),
            steps_per_epoch=math.ceil(X_train.shape[0]/batch_size_value),
            epochs=5, callbacks=[lr_finder])

        print("Finish LRFinder. Check the plot in {}".format(lrfinder_dir))
        sys.exit(0)
    else:
        results = model.fit(train_generator, validation_data=val_generator,
            validation_steps=math.ceil(val_generator.n/batch_size_value),
            steps_per_epoch=math.ceil(train_generator.n/batch_size_value),
            epochs=epochs_value, callbacks=[earlystopper, checkpointer, time_callback])

        print("Epoch average time: {}".format(np.mean(time_callback.times)))        
        print("Epoch number: {}".format(len(results.history['val_loss'])))          
        print("Train time (s): {}".format(np.sum(time_callback.times)))             
        print("Train loss: {}".format(np.min(results.history['loss'])))             
        print("Train accuracy: {}"                                                  
              .format(np.max(results.history['accuracy'])))                         
        print("Validation loss: {}".format(np.min(results.history['val_loss'])))    
        print("Validation accuracy: {}"                                             
              .format(np.max(results.history['val_accuracy'])))
else:
    h5_file=os.path.join(h5_dir, weight_files_prefix + previous_job_weights 
                         + '_' + str(args.run_id) + '.h5')
    print("Loading model weights from h5_file: {}".format(h5_file))
    model.load_weights(h5_file)

# Print confusion matrix and some metrics                                       
target_names = ['Background', 'Mitochondria']                                   
print('Validation-Confusion Matrix')                                            
preds_val = model.predict(val_generator, steps=len(val_generator), verbose=1)   
print(confusion_matrix(                                                         
          val_generator.classes, (preds_val>0.5).astype('uint8')))              
print(classification_report(                                                    
          val_generator.classes, (preds_val>0.5).astype('uint8'),               
          target_names=target_names))                                           


print("############################################\n"                          
      "#  FINE TUNNING FOR SEMANTIC SEGMENTATION  #\n"                          
      "############################################\n")                         
                                                                                
model_test_ft = cnn_oztel_test(model, crop_shape, lr=learning_rate_value,       
                               optimizer=optimizer)                             
# Check the network created                                                     
model_test_ft.summary(line_length=150)                                          
save_filters_of_convlayer(model, char_dir, name="conv1", prefix="model")        
save_filters_of_convlayer(model_test_ft, char_dir, name="conv1", prefix="model_test_ft")
                                                                                
h5_file=os.path.join(h5_dir, weight_files_prefix + job_identifier + '_ft.h5')   
                                                                                
# Prepare the train/val generator with softmax output                           
X_train = load_data_from_dir(p_train_ss_x, crop_shape)                          
Y_train = load_data_from_dir(p_train_ss_y, crop_shape)                          
X_val = load_data_from_dir(p_val_ss_x, crop_shape)                              
Y_val = load_data_from_dir(p_val_ss_y, crop_shape)                              
                                                                                
data_gen_args = dict(                                                           
    X=X_train, Y=Y_train, batch_size=batch_size_value,                          
    shape=(crop_shape[0],crop_shape[1],1),                            
    shuffle=shuffle_train_data_each_epoch, da=False, softmax_out=softmax_out)   
                                                                                
data_gen_val_args = dict(                                                       
    X=X_val, Y=Y_val, batch_size=batch_size_value,                              
    shape=(crop_shape[0],crop_shape[1],1),
    shuffle=shuffle_val_data_each_epoch, da=False, softmax_out=softmax_out)     
                                                                                
train_generator = ImageDataGenerator(**data_gen_args)                           
val_generator = ImageDataGenerator(**data_gen_val_args)                         
                                                                                
if not load_previous_weights_ft:                                           
    checkpointer = ModelCheckpoint(                                             
            os.path.join(h5_dir, weight_files_prefix + job_identifier + '_ft.h5'),
            verbose=1, save_best_only=True)                                     
    results = model_test_ft.fit(                                                
        train_generator, validation_data=val_generator,                         
        validation_steps=len(val_generator), steps_per_epoch=len(train_generator),
        epochs=epochs_value, callbacks=[earlystopper, checkpointer, time_callback])
else:                                                                           
    print("Loading model_test_ft weights from h5_file: {}".format(h5_file))     
    model_test_ft.load_weights(h5_file)   


print("################################\n"
      "#  PREPARE DATA FOR INFERENCE  #\n"
      "################################\n")

# Prepare test data for its use
Y_test /= 255 if np.max(Y_test) > 2 else Y_test
X_test /= 255 if np.max(X_test) > 2 else X_test
if softmax_out:
    Y_test_one_hot = np.zeros(Y_test.shape[:3] + (2,))
    for i in range(Y_test.shape[0]):
        Y_test_one_hot[i] = np.asarray(img_to_onehot_encoding(Y_test[i]))
    Y_test = Y_test_one_hot


print("##########################\n"
      "#  INFERENCE (per crop)  #\n"
      "##########################\n")

if random_crops_in_DA:
    X_test, Y_test = crop_data_with_overlap(
        X_test, Y_test, crop_shape[0], test_ov_crops)

print("Evaluating test data . . .")
score_per_crop = model_test_ft.evaluate(
    X_test, Y_test, batch_size=batch_size_value, verbose=1)
loss_per_crop = score_per_crop[0]
jac_per_crop = score_per_crop[1]

print("Making the predictions on test data . . .")
preds_test = model_test_ft.predict(X_test, batch_size=batch_size_value, verbose=1)

if softmax_out:
    preds_test = np.expand_dims(preds_test[...,1], -1)
    Y_test = np.expand_dims(Y_test[...,1], -1)


print("########################################\n"
      "#  Metrics (per image, merging crops)  #\n"
      "########################################\n")

# Merge crops
if make_crops or (random_crops_in_DA and test_ov_crops == 1):
    h_num = math.ceil(orig_test_shape[1]/preds_test.shape[1])
    v_num = math.ceil(orig_test_shape[2]/preds_test.shape[2])

    print("Reconstruct preds_test . . .")
    preds_test = merge_data_without_overlap(
        preds_test, math.ceil(preds_test.shape[0]/(h_num*v_num)),
        out_shape=[h_num, v_num], grid=False)
    print("Reconstruct X_test . . .")
    X_test = merge_data_without_overlap(
        X_test, math.ceil(X_test.shape[0]/(h_num*v_num)),
        out_shape=[h_num, v_num], grid=False)
    print("Reconstruct Y_test . . .")
    Y_test = merge_data_without_overlap(
        Y_test, math.ceil(Y_test.shape[0]/(h_num*v_num)),
        out_shape=[h_num, v_num], grid=False)
elif random_crops_in_DA and test_ov_crops > 1:
    print("Reconstruct X_test . . .")
    X_test = merge_data_with_overlap(
        X_test, orig_test_shape, crop_shape[0], test_ov_crops)
    print("Reconstruct Y_test . . .")
    Y_test = merge_data_with_overlap(
        Y_test, orig_test_shape, crop_shape[0], test_ov_crops)
    print("Reconstruct preds_test . . .")
    preds_test = merge_data_with_overlap(
        preds_test, orig_test_shape, crop_shape[0], test_ov_crops)

print("Saving predicted images . . .")
save_img(Y=(preds_test > 0.5).astype(np.uint8),
         mask_dir=result_bin_dir_per_image, prefix="test_out_bin")
save_img(Y=preds_test, mask_dir=result_no_bin_dir_per_image,
         prefix="test_out_no_bin")

print("Calculate metrics (per image) . . .")
jac_per_image = jaccard_index_numpy(Y_test, (preds_test > 0.5).astype(np.uint8))
voc_per_image = voc_calculation(
    Y_test, (preds_test > 0.5).astype(np.uint8), jac_per_image)
det_per_image = DET_calculation(
    Y_test, (preds_test > 0.5).astype(np.uint8), det_eval_ge_path, det_eval_path,
    det_bin, n_dig, args.job_id)

print("~~~~ Smooth (per image) ~~~~")
Y_test_smooth = np.zeros(X_test.shape, dtype=np.float32)
for i in tqdm(range(X_test.shape[0])):
    predictions_smooth = predict_img_with_smooth_windowing(
        X_test[i], window_size=crop_shape[0], subdivisions=2, nb_classes=1,
        pred_func=(lambda img_batch_subdiv: model_test_ft.predict(img_batch_subdiv)),
        softmax=softmax_out)
    Y_test_smooth[i] = predictions_smooth

print("Saving smooth predicted images . . .")
save_img(Y=Y_test_smooth, mask_dir=smo_no_bin_dir_per_image,
         prefix="test_out_smo_no_bin")
save_img(Y=(Y_test_smooth > 0.5).astype(np.uint8), mask_dir=smo_bin_dir_per_image,
         prefix="test_out_smo")

print("Calculate metrics (smooth + per crop) . . .")
smo_score_per_image = jaccard_index_numpy(
    Y_test, (Y_test_smooth > 0.5).astype(np.uint8))
smo_voc_per_image = voc_calculation(
    Y_test, (Y_test_smooth > 0.5).astype(np.uint8), smo_score_per_image)
smo_det_per_image = DET_calculation(
    Y_test, (Y_test_smooth > 0.5).astype(np.uint8), det_eval_ge_path,
    det_eval_post_path, det_bin, n_dig, args.job_id)

print("~~~~ Z-Filtering (per image) ~~~~")
zfil_preds_test = calculate_z_filtering(preds_test)

print("Saving Z-filtered images . . .")
save_img(Y=zfil_preds_test, mask_dir=zfil_dir_per_image, prefix="test_out_zfil")

print("Calculate metrics (Z-filtering + per crop) . . .")
zfil_score_per_image = jaccard_index_numpy(
    Y_test, (zfil_preds_test > 0.5).astype(np.uint8))
zfil_voc_per_image = voc_calculation(
    Y_test, (zfil_preds_test > 0.5).astype(np.uint8), zfil_score_per_image)
zfil_det_per_image = DET_calculation(
    Y_test, (zfil_preds_test > 0.5).astype(np.uint8), det_eval_ge_path,
    det_eval_post_path, det_bin, n_dig, args.job_id)
del zfil_preds_test, preds_test

print("~~~~ Smooth + Z-Filtering (per image) ~~~~")
smo_zfil_preds_test = calculate_z_filtering(Y_test_smooth)

print("Saving smoothed + Z-filtered images . . .")
save_img(Y=smo_zfil_preds_test, mask_dir=smo_zfil_dir_per_image,
         prefix="test_out_smoo_zfil")

print("Calculate metrics (Smooth + Z-filtering per crop) . . .")
smo_zfil_score_per_image = jaccard_index_numpy(
    Y_test, (smo_zfil_preds_test > 0.5).astype(np.uint8))
smo_zfil_voc_per_image = voc_calculation(
    Y_test, (smo_zfil_preds_test > 0.5).astype(np.uint8),
    smo_zfil_score_per_image)
smo_zfil_det_per_image = DET_calculation(
    Y_test, (smo_zfil_preds_test > 0.5).astype(np.uint8),
    det_eval_ge_path, det_eval_post_path, det_bin, n_dig, args.job_id)

del Y_test_smooth, smo_zfil_preds_test


print("############################################################\n"
      "#  Metrics (per image, merging crops with 50% of overlap)  #\n"
      "############################################################\n")

Y_test_50ov = np.zeros(X_test.shape, dtype=np.float32)
for i in tqdm(range(X_test.shape[0])):
    predictions_smooth = predict_img_with_overlap(
        X_test[i], window_size=crop_shape[0], subdivisions=2,
        nb_classes=1, pred_func=(
            lambda img_batch_subdiv: model_test_ft.predict(img_batch_subdiv)),
        softmax=softmax_out)
    Y_test_50ov[i] = predictions_smooth

print("Saving 50% overlap predicted images . . .")
save_img(Y=(Y_test_50ov > 0.5).astype(np.float32),
         mask_dir=result_bin_dir_50ov, prefix="test_out_bin_50ov")
save_img(Y=Y_test_50ov, mask_dir=result_no_bin_dir_50ov,
         prefix="test_out_no_bin_50ov")

print("Calculate metrics (50% overlap) . . .")
jac_50ov = jaccard_index_numpy(Y_test, (Y_test_50ov > 0.5).astype(np.float32))
voc_50ov = voc_calculation(
    Y_test, (Y_test_50ov > 0.5).astype(np.float32), jac_50ov)
det_50ov = DET_calculation(
    Y_test, (Y_test_50ov > 0.5).astype(np.float32), det_eval_ge_path,
    det_eval_path, det_bin, n_dig, args.job_id)

print("~~~~ Z-Filtering (50% overlap) ~~~~")
zfil_preds_test = calculate_z_filtering(Y_test_50ov)

print("Saving Z-filtered images . . .")
save_img(Y=zfil_preds_test, mask_dir=zfil_dir_50ov, prefix="test_out_zfil")

print("Calculate metrics (Z-filtering + 50% overlap) . . .")
zfil_score_50ov = jaccard_index_numpy(
    Y_test, (zfil_preds_test > 0.5).astype(np.uint8))
zfil_voc_50ov = voc_calculation(
    Y_test, (zfil_preds_test > 0.5).astype(np.uint8), zfil_score_50ov)
zfil_det_50ov = DET_calculation(
    Y_test, (zfil_preds_test > 0.5).astype(np.uint8), det_eval_ge_path,
    det_eval_post_path, det_bin, n_dig, args.job_id)
del Y_test_50ov, zfil_preds_test


print("########################\n"
      "# Metrics (full image) #\n"
      "########################\n")

print("Making the predictions on test data . . .")
preds_test_full = model_test_ft.predict(X_test, batch_size=batch_size_value, verbose=1)

if softmax_out:
    preds_test_full = np.expand_dims(preds_test_full[...,1], -1)

print("Saving predicted images . . .")
save_img(Y=(preds_test_full > 0.5).astype(np.uint8),
         mask_dir=result_bin_dir_full, prefix="test_out_bin_full")
save_img(Y=preds_test_full, mask_dir=result_no_bin_dir_full,
         prefix="test_out_no_bin_full")

print("Calculate metrics (full image) . . .")
jac_full = jaccard_index_numpy(Y_test, (preds_test_full > 0.5).astype(np.uint8))
voc_full = voc_calculation(Y_test, (preds_test_full > 0.5).astype(np.uint8),
                           jac_full)
det_full = DET_calculation(
    Y_test, (preds_test_full > 0.5).astype(np.uint8), det_eval_ge_path,
    det_eval_path, det_bin, n_dig, args.job_id)

print("~~~~ 8-Ensemble (full image) ~~~~")
Y_test_smooth = np.zeros(X_test.shape, dtype=(np.float32))

for i in tqdm(range(X_test.shape[0])):
    predictions_smooth = ensemble8_2d_predictions(X_test[i],
        pred_func=(lambda img_batch_subdiv: model_test_ft.predict(img_batch_subdiv)),
        softmax_output=softmax_out)
    Y_test_smooth[i] = predictions_smooth

print("Saving smooth predicted images . . .")
save_img(Y=Y_test_smooth, mask_dir=smo_no_bin_dir_full,
         prefix="test_out_ens_no_bin")
save_img(Y=(Y_test_smooth > 0.5).astype(np.uint8), mask_dir=smo_bin_dir_full,
         prefix="test_out_ens")

print("Calculate metrics (8-Ensemble + full image) . . .")
smo_score_full = jaccard_index_numpy(
    Y_test, (Y_test_smooth > 0.5).astype(np.uint8))
smo_voc_full = voc_calculation(
    Y_test, (Y_test_smooth > 0.5).astype(np.uint8), smo_score_full)
smo_det_full = DET_calculation(
    Y_test, (Y_test_smooth > 0.5).astype(np.uint8), det_eval_ge_path,
    det_eval_path, det_bin, n_dig, args.job_id)
del Y_test_smooth

print("~~~~ Z-Filtering (full image) ~~~~")
zfil_preds_test = calculate_z_filtering(preds_test_full)

print("Saving Z-filtered images . . .")
save_img(Y=zfil_preds_test, mask_dir=zfil_dir_full, prefix="test_out_zfil")

print("Calculate metrics (Z-filtering + full image) . . .")
zfil_score_full = jaccard_index_numpy(
    Y_test, (zfil_preds_test > 0.5).astype(np.uint8))
zfil_voc_full = voc_calculation(
    Y_test, (zfil_preds_test > 0.5).astype(np.uint8), zfil_score_full)
zfil_det_full = DET_calculation(
    Y_test, (zfil_preds_test > 0.5).astype(np.uint8), det_eval_ge_path,
    det_eval_post_path, det_bin, n_dig, args.job_id)

del zfil_preds_test

print("~~~~ Spurious Detection (full image) ~~~~")
spu_preds_test = spuriuous_detection_filter(preds_test_full)

print("Saving spurious detection filtering resulting images . . .")
save_img(Y=spu_preds_test, mask_dir=spu_dir_full, prefix="test_out_spu")

print("Calculate metrics (Spurious + full image) . . .")
spu_score_full = jaccard_index_numpy(Y_test, spu_preds_test)
spu_voc_full = voc_calculation(Y_test, spu_preds_test, spu_score_full)
spu_det_full = DET_calculation(Y_test, spu_preds_test, det_eval_ge_path,
                               det_eval_post_path, det_bin, n_dig, args.job_id)

print("~~~~ Watershed (full image) ~~~~")
wa_preds_test = boundary_refinement_watershed2(
    preds_test_full, (preds_test_full> 0.5).astype(np.uint8),
    save_marks_dir=wa_debug_dir_full)
    #X_test, (preds_test> 0.5).astype(np.uint8), save_marks_dir=watershed_debug_dir)

print("Saving watershed resulting images . . .")
save_img(Y=(wa_preds_test).astype(np.uint8), mask_dir=wa_dir_full,
         prefix="test_out_wa")

print("Calculate metrics (Watershed + full image) . . .")
wa_score_full = jaccard_index_numpy(Y_test, wa_preds_test)
wa_voc_full = voc_calculation(Y_test, wa_preds_test, wa_score_full)
wa_det_full = DET_calculation(Y_test, wa_preds_test, det_eval_ge_path,
                              det_eval_post_path, det_bin, n_dig, args.job_id)
del preds_test_full, wa_preds_test

print("~~~~ Spurious Detection + Watershed + Z-filtering (full image) ~~~~")
# Use spu_preds_test
spu_wa_zfil_preds_test = boundary_refinement_watershed2(
    spu_preds_test, (spu_preds_test> 0.5).astype(np.uint8),
    save_marks_dir=spu_wa_zfil_wa_debug_dir)
    #X_test, (preds_test> 0.5).astype(np.uint8), save_marks_dir=watershed_debug_dir)

spu_wa_zfil_preds_test = calculate_z_filtering(spu_wa_zfil_preds_test)

print("Saving Z-filtered images . . .")
save_img(Y=spu_wa_zfil_preds_test, mask_dir=spu_wa_zfil_dir_full,
         prefix="test_out_spu_wa_zfil")

print("Calculate metrics (Z-filtering + full image) . . .")
spu_wa_zfil_score_full = jaccard_index_numpy(
    Y_test, (spu_wa_zfil_preds_test > 0.5).astype(np.uint8))
spu_wa_zfil_voc_full = voc_calculation(
    Y_test, (spu_wa_zfil_preds_test > 0.5).astype(np.uint8),
    spu_wa_zfil_score_full)
spu_wa_zfil_det_full = DET_calculation(
    Y_test, (spu_wa_zfil_preds_test > 0.5).astype(np.uint8), det_eval_ge_path,
    det_eval_post_path, det_bin, n_dig, args.job_id)
del spu_wa_zfil_preds_test, spu_preds_test


print("####################################\n"
      "#  PRINT AND SAVE SCORES OBTAINED  #\n"
      "####################################\n")

if not load_previous_weights:
    print("Epoch average time: {}".format(np.mean(time_callback.times)))
    print("Epoch number: {}".format(len(results.history['val_loss'])))
    print("Train time (s): {}".format(np.sum(time_callback.times)))
    print("Train loss: {}".format(np.min(results.history['loss'])))
    print("Train IoU: {}".format(np.max(results.history['jaccard_index'])))
    print("Validation loss: {}".format(np.min(results.history['val_loss'])))
    print("Validation IoU: {}"
          .format(np.max(results.history['val_jaccard_index'])))

print("Test loss: {}".format(loss_per_crop))
print("Test IoU (per crop): {}".format(jac_per_crop))

print("Test IoU (merge into complete image): {}".format(jac_per_image))
print("Test VOC (merge into complete image): {}".format(voc_per_image))
print("Test DET (merge into complete image): {}".format(det_per_image))
print("Post-process: Smooth - Test IoU (merge into complete image): {}".format(smo_score_per_image))
print("Post-process: Smooth - Test VOC (merge into complete image): {}".format(smo_voc_per_image))
print("Post-process: Smooth - Test DET (merge into complete image): {}".format(smo_det_per_image))
print("Post-process: Z-Filtering - Test IoU (merge into complete image): {}".format(zfil_score_per_image))
print("Post-process: Z-Filtering - Test VOC (merge into complete image): {}".format(zfil_voc_per_image))
print("Post-process: Z-Filtering - Test DET (merge into complete image): {}".format(zfil_det_per_image))
print("Post-process: Smooth + Z-Filtering - Test IoU (merge into complete image): {}".format(smo_zfil_score_per_image))
print("Post-process: Smooth + Z-Filtering - Test VOC (merge into complete image): {}".format(smo_zfil_voc_per_image))
print("Post-process: Smooth + Z-Filtering - Test DET (merge into complete image): {}".format(smo_zfil_det_per_image))

print("Test IoU (merge with 50% overlap): {}".format(jac_50ov))
print("Test VOC (merge with 50% overlap): {}".format(voc_50ov))
print("Test DET (merge with with 50% overlap): {}".format(det_50ov))
print("Post-process: Z-Filtering - Test IoU (merge with 50% overlap): {}".format(zfil_score_50ov))
print("Post-process: Z-Filtering - Test VOC (merge with 50% overlap): {}".format(zfil_voc_50ov))
print("Post-process: Z-Filtering - Test DET (merge with 50% overlap): {}".format(zfil_det_50ov))

print("Test IoU (full): {}".format(jac_full))
print("Test VOC (full): {}".format(voc_full))
print("Test DET (full): {}".format(det_full))
print("Post-process: Ensemble - Test IoU (full): {}".format(smo_score_full))
print("Post-process: Ensemble - Test VOC (full): {}".format(smo_voc_full))
print("Post-process: Ensemble - Test DET (full): {}".format(smo_det_full))
print("Post-process: Z-Filtering - Test IoU (full): {}".format(zfil_score_full))
print("Post-process: Z-Filtering - Test VOC (full): {}".format(zfil_voc_full))
print("Post-process: Z-Filtering - Test DET (full): {}".format(zfil_det_full))
print("Post-process: Spurious Detection - Test IoU (full): {}".format(spu_score_full))
print("Post-process: Spurious Detection - VOC (full): {}".format(spu_voc_full))
print("Post-process: Spurious Detection - DET (full): {}".format(spu_det_full))
print("Post-process: Watershed - Test IoU (full): {}".format(wa_score_full))
print("Post-process: Watershed - VOC (full): {}".format(wa_voc_full))
print("Post-process: Watershed - DET (full): {}".format(wa_det_full))
print("Post-process: Spurious + Watershed + Z-Filtering - Test IoU (full): {}".format(spu_wa_zfil_score_full))
print("Post-process: Spurious + Watershed + Z-Filtering - Test VOC (full): {}".format(spu_wa_zfil_voc_full))
print("Post-process: Spurious + Watershed + Z-Filtering - Test DET (full): {}".format(spu_wa_zfil_det_full))

if not load_previous_weights:
    scores = {}
    for name in dir():
        if not name.startswith('__') and ("_per_crop" in name or "_50ov" in name\
        or "_per_image" in name or "_full" in name):
            scores[name] = eval(name)

    store_history(results, scores, time_callback, args.result_dir, job_identifier)
    create_plots(results, job_identifier, char_dir)

print("FINISHED JOB {} !!".format(job_identifier))

