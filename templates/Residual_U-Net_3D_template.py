# Script based on 3D_template.py


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
                 calculate_3D_volume_prob_map
limit_threads()

# Try to generate the results as reproducible as possible
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
from data_manipulation import load_and_prepare_3D_data, check_binary_masks, \
                              merge_3D_data_with_overlap
from data_3D_generators import VoxelDataGenerator
from networks.resunet_3d import ResUNet_3D
from metrics import jaccard_index_numpy, voc_calculation
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
from tensorflow.keras.models import load_model
from tqdm import tqdm
from smooth_tiled_predictions import predict_img_with_smooth_windowing, \
                                     predict_img_with_overlap,\
                                     smooth_3d_predictions
from tensorflow.keras.utils import plot_model
from post_processing import spuriuous_detection_filter, calculate_z_filtering,\
                            boundary_refinement_watershed2
from LRFinder.keras_callback import LRFinder


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
perc_used_as_val = 0.1
# Create the validation data with random images of the training data. If False
# the validation data will be the last portion of training images.
random_val_data = False


### Dataset shape
# Note: train and test dimensions must be the same when training the network and
# making the predictions. Be sure to take care of this if you are not going to
# use "crop_data()" with the arg force_shape, as this function resolves the 
# problem creating always crops of the same dimension
img_train_shape = (1024, 768, 1)
img_test_shape = (1024, 768, 1)


### 3D volume variables
# Train shape of the 3D subvolumes
train_3d_desired_shape = (80, 80, 80, 1)
# Train shape of the 3D subvolumes
test_3d_desired_shape = (80, 80, 80, 1)
# Percentage of overlap made to create subvolumes of the defined shape based on
# test data. Fix in 0.0 to calculate the minimun overlap needed to satisfy the
# shape.
ov_test = 0.0


### Normalization
# Flag to normalize the data dividing by the mean pixel value
normalize_data = False                                                          
# Force the normalization value to the given number instead of the mean pixel 
# value
norm_value_forced = -1                                                          


### Data augmentation (DA) variables. Based on https://github.com/aleju/imgaug
# Flag to activate DA
da = True
# Create samples of the DA made. Useful to check the output images made.
aug_examples = True
# Flag to shuffle the training data on every epoch 
shuffle_train_data_each_epoch = True
# Flag to shuffle the validation data on every epoch
shuffle_val_data_each_epoch = False
# Histogram equalization
hist_eq = False
# Rotation of 90º to the subvolumes
rotation = True
# Flag to make flips on the subvolumes (horizontal and vertical)
flips = True
# Elastic transformations
elastic = True
# Gaussian blur
g_blur = False
# Gamma contrast 
gamma_contrast = False
# Flag to extract random subvolumnes during the DA
random_subvolumes_in_DA = False
# Calculate probability map to make random subvolumes to be extracted with high
# probability of having a mitochondria on the middle of it. Useful to avoid
# extracting a subvolume which less mitochondria information.
probability_map = False # Only active with random_subvolumes_in_DA
w_foreground = 0.94 # Only active with probability_map
w_background = 0.06 # Only active with probability_map


### Extra train data generation
# Number of times to duplicate the train data. Useful when 
# "random_subvolumes_in_DA" is made, as more original train data can be cover
duplicate_train = 0
# Extra number of images to add to the train data. Applied after duplicate_train
extra_train_data = 0


### Load previously generated model weigths
# Flag to activate the load of a previous training weigths instead of train 
# the network again
load_previous_weights = False
# ID of the previous experiment to load the weigths from 
previous_job_weights = args.job_id
# Flag to activate the fine tunning
fine_tunning = False
# ID of the previous weigths to load the weigths from to make the fine tunning 
fine_tunning_weigths = args.job_id
# Prefix of the files where the weights are stored/loaded from
weight_files_prefix = 'model.fibsem_'
# Wheter to find the best learning rate plot. If this options is selected the 
# training will stop when 5 epochs are done
use_LRFinder = False


### Experiment main parameters
# Batch size value
batch_size_value = 1
# Optimizer to use. Possible values: "sgd" or "adam"
optimizer = "adam"
# Learning rate used by the optimization method
learning_rate_value = 0.0001
# Number of epochs to train the network
epochs_value = 400
# Number of epochs to stop the training process after no improvement
patience = 400


### Network architecture specific parameters
# Number of feature maps on each level of the network
feature_maps = [28, 36, 48, 64]
# Depth of the network
depth = 3
# Flag to activate the Spatial Dropout instead of use the "normal" dropout layer
spatial_dropout = False
# Values to make the dropout with. It's dimension must be equal depth+1. Set to
# 0 to prevent dropout
dropout_values = [0.1, 0.1, 0.1, 0.1]
# Flag to active batch normalization
batch_normalization = False
# Kernel type to use on convolution layers
kernel_init = 'he_normal'
# Activation function to use
activation = "elu"
# Active flag if softmax is used as the last layer of the network
softmax_out = False


### Paths of the results                                             
# Directory where predicted images of the segmentation will be stored
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


print("###############\n"
      "#  LOAD DATA  #\n"
      "###############\n")

X_train, Y_train, X_val,\
Y_val, X_test, Y_test,\
orig_test_shape, norm_value = load_and_prepare_3D_data(
    train_path, train_mask_path, test_path, test_mask_path, img_train_shape,
    img_test_shape, val_split=perc_used_as_val, create_val=True,
    shuffle_val=random_val_data, random_subvolumes_in_DA=random_subvolumes_in_DA,
    train_subvol_shape=train_3d_desired_shape,
    test_subvol_shape=test_3d_desired_shape, ov_test=ov_test)

# Normalize the data
if normalize_data == True:
    if norm_value_forced != -1: 
        print("Forced normalization value to {}".format(norm_value_forced))
        norm_value = norm_value_forced
    else:
        print("Normalization value calculated: {}".format(norm_value))
    X_train -= int(norm_value)
    X_val -= int(norm_value)
    X_test -= int(norm_value)
    

print("###########################\n"
      "#  EXTRA DATA GENERATION  #\n"
      "###########################\n")

# Calculate the steps_per_epoch value to train in case
if duplicate_train != 0:
    steps_per_epoch_value = int((duplicate_train*X_train.shape[0])/batch_size_value)
    print("Data doubled by {} ; Steps per epoch = {}".format(duplicate_train,
          steps_per_epoch_value))
else:
    steps_per_epoch_value = int(X_train.shape[0]/batch_size_value)

# Add extra train data generated with DA
if extra_train_data != 0:
    extra_generator = VoxelDataGenerator(
        X_train, Y_train, random_subvolumes_in_DA=random_subvolumes_in_DA,
        shuffle_each_epoch=True, batch_size=batch_size_value, da=da, 
        hist_eq=hist_eq, flip=flips, rotation=rotation, elastic=elastic, 
        g_blur=g_blur, gamma_contrast=gamma_contrast)

    extra_x, extra_y = extra_generator.get_transformed_samples(extra_train_data)

    X_train = np.vstack((X_train, extra_x*255))
    Y_train = np.vstack((Y_train, extra_y*255))
    print("{} extra train data generated, the new shape of the train now is {}"\
          .format(extra_train_data, X_train.shape))


print("#######################\n"
      "#  DATA AUGMENTATION  #\n"
      "#######################\n")

# Calculate the probability map per image
train_prob = None
if probability_map == True:
    prob_map_file = os.path.join(prob_map_dir, 'prob_map.npy')
    if os.path.exists(prob_map_dir):
        train_prob = np.load(prob_map_file)
    else:
        train_prob = calculate_3D_volume_prob_map(
            Y_train, w_foreground, w_background, save_file=prob_map_file)

print("Preparing validation data generator . . .")
val_generator = VoxelDataGenerator(
    X_val, Y_val, random_subvolumes_in_DA=random_subvolumes_in_DA,
    subvol_shape=train_3d_desired_shape,
    shuffle_each_epoch=shuffle_val_data_each_epoch, batch_size=batch_size_value,
    da=False, softmax_out=softmax_out, val=True)
del X_val, Y_val

print("Preparing train data generator . . .")
train_generator = VoxelDataGenerator(
    X_train, Y_train, random_subvolumes_in_DA=random_subvolumes_in_DA,
    shuffle_each_epoch=shuffle_train_data_each_epoch, 
    batch_size=batch_size_value, da=da, hist_eq=hist_eq, flip=flips, 
    rotation=rotation, elastic=elastic, g_blur=g_blur, 
    gamma_contrast=gamma_contrast, softmax_out=softmax_out, prob_map=train_prob,
    extra_data_factor=duplicate_train)
del X_train, Y_train

# Create the test data generator without DA
print("Preparing test data generator . . .")
test_generator = VoxelDataGenerator(
    X_test, Y_test, random_subvolumes_in_DA=False, shuffle_each_epoch=False,
    batch_size=batch_size_value, da=False, softmax_out=softmax_out)

# Generate examples of data augmentation
if aug_examples == True:
    train_generator.get_transformed_samples(
        5, random_images=False, save_to_dir=True, out_dir=da_samples_dir)


print("#################################\n"
      "#  BUILD AND TRAIN THE NETWORK  #\n"
      "#################################\n")

print("Creating the network . . .")
model = ResUNet_3D(train_3d_desired_shape, activation=activation, depth=depth,
                   feature_maps=feature_maps, drop_values=dropout_values,
                   batch_norm=batch_normalization, k_init=kernel_init, 
                   optimizer=optimizer, lr=learning_rate_value)

# Check the network created
model.summary(line_length=150)
os.makedirs(char_dir, exist_ok=True)
model_name = os.path.join(char_dir, "model_plot_" + job_identifier + ".png")
plot_model(model, to_file=model_name, show_shapes=True, show_layer_names=True)

if load_previous_weights == False:
    if fine_tunning == True:                                                    
        h5_file=os.path.join(h5_dir, weight_files_prefix + fine_tunning_weigths 
                             + '_' + str(args.run_id) + '.h5')     
        print("Fine-tunning: loading model weights from h5_file: {}"
              .format(h5_file))   
        model.load_weights(h5_file)                                             

    if use_LRFinder:
        print("Training just for 10 epochs . . .")
        results = model.fit(x=train_generator, validation_data=val_generator,
                            validation_steps=len(val_generator), 
                            steps_per_epoch=len(train_generator), epochs=5, 
                            callbacks=[lr_finder])
        print("Finish LRFinder. Check the plot in {}".format(lrfinder_dir))
        sys.exit(0)
    else:
        results = model.fit(x=train_generator, validation_data=val_generator,
            validation_steps=len(val_generator), 
            steps_per_epoch=steps_per_epoch_value, epochs=epochs_value,
            callbacks=[earlystopper, checkpointer, time_callback])
else:
    h5_file=os.path.join(h5_dir, weight_files_prefix + previous_job_weights 
                                 + '_' + str(args.run_id) + '.h5')
    print("Loading model weights from h5_file: {}".format(h5_file))
    model.load_weights(h5_file)


print("################################\n"
      "#  PREPARE DATA FOR INFERENCE  #\n"
      "################################\n")

# Prepare test data for its use
Y_test /= 255 if np.max(Y_test) > 2 else Y_test
X_test /= 255 if np.max(X_test) > 2 else X_test


print("##########################\n"
      "#  INFERENCE (per crop)  #\n"
      "##########################\n")

# Evaluate to obtain the loss value and the Jaccard index
print("Evaluating test data . . .")
score_per_crop = model.evaluate(test_generator, verbose=1)
loss_per_crop = score_per_crop[0]
jac_per_crop = score_per_crop[1]

print("Making the predictions on test data . . .")
preds_test = model.predict(test_generator, verbose=1)

if softmax_out:
    preds_test = np.expand_dims(preds_test[...,1], -1)


print("####################################################\n"
      "#  Metrics (per image, merging subvolumes - 50ov)  #\n"
      "####################################################\n")

# Merge the volumes and convert them into 2D data                               
preds_test, Y_test = merge_3D_data_with_overlap(                                 
    preds_test, orig_test_shape, data_mask=Y_test, overlap_z=ov_test) 

print("Saving predicted images . . .")                                          
save_img(Y=(preds_test > 0.5).astype(np.uint8), 
         mask_dir=result_bin_dir_per_image, prefix="test_out_bin")                                                 
save_img(Y=preds_test, mask_dir=result_no_bin_dir_per_image, 
         prefix="test_out_no_bin")     
                                                                                
print("Calculate metrics (per image) . . .")                                                
jac_per_image = jaccard_index_numpy(                                        
    Y_test, (preds_test > 0.5).astype(np.uint8))                                 
voc_per_image = voc_calculation(                                            
    Y_test, (preds_test > 0.5).astype(np.uint8), jac_per_image)              
det_per_image = -1

print("~~~~ 16-Ensemble (per image) ~~~~")                                     
Y_test_smooth = np.zeros(X_test.shape, dtype=np.float32)                        
for i in tqdm(range(X_test.shape[0])):                                          
    predictions_smooth = smooth_3d_predictions(X_test[i],                       
        pred_func=(lambda img_batch_subdiv: model.predict(img_batch_subdiv)),
        softmax=softmax_out)   
                                                                                
    Y_test_smooth[i] = predictions_smooth                                       
                                                                                
# Merge the volumes and convert them into 2D data                               
Y_test_smooth = merge_3D_data_with_overlap(                                     
    Y_test_smooth, orig_test_shape, overlap_z=ov_test)                          
                                                                                
print("Saving smooth predicted images . . .")                                   
save_img(Y=Y_test_smooth, mask_dir=smo_no_bin_dir_per_image,
         prefix="test_out_smo_no_bin")                                       
save_img(Y=(Y_test_smooth > 0.5).astype(np.uint8), 
         mask_dir=smo_bin_dir_per_image, prefix="test_out_smo")                                              
                                                                                
print("Calculate metrics (smooth + per subvolume). . .")                        
smo_score_per_image = jaccard_index_numpy(                                  
    Y_test, (Y_test_smooth > 0.5).astype(np.uint8))                             
smo_voc_per_image = voc_calculation(                                        
    Y_test, (Y_test_smooth > 0.5).astype(np.uint8), smo_score_per_image)    
smo_det_per_image = -1

print("~~~~ Z-Filtering (per image) ~~~~")                                      
zfil_preds_test = calculate_z_filtering(preds_test)                             
                                                                                
print("Saving Z-filtered images . . .")                                         
save_img(Y=zfil_preds_test, mask_dir=zfil_dir_per_image, 
         prefix="test_out_zfil")
                                                                                
print("Calculate metrics (Z-filtering + per crop) . . .")                       
zfil_score_per_image = jaccard_index_numpy(                                     
    Y_test, (zfil_preds_test > 0.5).astype(np.uint8))                           
zfil_voc_per_image = voc_calculation(                                           
    Y_test, (zfil_preds_test > 0.5).astype(np.uint8), zfil_score_per_image)     
zfil_det_per_image = -1
del zfil_preds_test
                                                                                
print("~~~~ Smooth + Z-Filtering (per subvolume) ~~~~")                             
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
smo_zfil_det_per_image = -1
del Y_test_smooth, smo_zfil_preds_test                                          
                                        

print("############################################################\n"
      "#  Metrics (per image, merging crops with 50% of overlap)  #\n"
      "############################################################\n")

jac_50ov = -1
voc_50ov = -1
det_50ov = -1

zfil_score_50ov = -1
zfil_voc_50ov = -1
zfil_det_50ov = -1


print("########################\n"
      "# Metrics (full image) #\n"
      "########################\n")

jac_full = -1
voc_full = -1
det_full = -1

smo_score_full = -1
smo_voc_full = -1
smo_det_full = -1

zfil_score_full = -1
zfil_voc_full = -1
zfil_det_full = -1

print("~~~~ Spurious Detection (full image) ~~~~")
spu_preds_test = spuriuous_detection_filter(preds_test)

print("Saving spurious detection filtering resulting images . . .")
save_img(Y=spu_preds_test, mask_dir=spu_dir_full, prefix="test_out_spu")

print("Calculate metrics (Spurious + full image) . . .")
spu_score_full = jaccard_index_numpy(Y_test, spu_preds_test)
spu_voc_full = voc_calculation(Y_test, spu_preds_test, spu_score_full)
spu_det_full = -1
              
print("~~~~ Watershed (full image) ~~~~")
wa_preds_test = boundary_refinement_watershed2(
    preds_test, (preds_test > 0.5).astype(np.uint8),
    save_marks_dir=wa_debug_dir_full)
    #X_test, (preds_test> 0.5).astype(np.uint8), save_marks_dir=watershed_debug_dir)

print("Saving watershed resulting images . . .")
save_img(Y=(wa_preds_test).astype(np.uint8), mask_dir=wa_dir_full,
         prefix="test_out_wa")

print("Calculate metrics (Watershed + full image) . . .")
wa_score_full = jaccard_index_numpy(Y_test, wa_preds_test)
wa_voc_full = voc_calculation(Y_test, wa_preds_test, wa_score_full)
wa_det_full = -1
del preds_test, wa_preds_test

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
spu_wa_zfil_det_full = -1
del spu_wa_zfil_preds_test, spu_preds_test


print("####################################\n"
      "#  PRINT AND SAVE SCORES OBTAINED  #\n"
      "####################################\n")

if load_previous_weights == False:
    print("Epoch average time: {}".format(np.mean(time_callback.times)))
    print("Epoch number: {}".format(len(results.history['val_loss'])))
    print("Train time (s): {}".format(np.sum(time_callback.times)))
    print("Train loss: {}".format(np.min(results.history['loss'])))
    print("Train IoU: {}".format(np.max(results.history['jaccard_index'])))
    print("Validation loss: {}".format(np.min(results.history['val_loss'])))
    print("Validation IoU: {}".format(np.max(results.history['val_jaccard_index'])))

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

    store_history(results, scores, time_callback, args.result_dir, job_identifier, 
                  metric="jaccard_index")
    create_plots(results, job_identifier, char_dir, metric="jaccard_index")

print("FINISHED JOB {} !!".format(job_identifier))
