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
                 TimeHistory, threshold_plots, save_img
limit_threads()

# Try to generate the results as reproducible as possible
set_seed(42)

crops_made = False
job_identifier = args.job_id + '_' + str(args.run_id)


##########################
#        IMPORTS         #
##########################

import random
import numpy as np
import math
import time
import tensorflow as tf
from data_manipulation import load_data, crop_data, merge_data_without_overlap,\
                              crop_data_with_overlap, merge_data_with_overlap, \
                              check_binary_masks
from data_generators import keras_da_generator, ImageDataGenerator,\
                            keras_gen_samples, calculate_z_filtering
from networks.unet import U_Net
from metrics import jaccard_index, jaccard_index_numpy, voc_calculation,\
                    DET_calculation
from itertools import chain
from tensorflow.keras.callbacks import EarlyStopping
from tensorflow.keras.models import load_model
from PIL import Image
from tqdm import tqdm
from smooth_tiled_predictions import predict_img_with_smooth_windowing, \
                                     predict_img_with_overlap
from skimage.segmentation import clear_border
from tensorflow.keras.utils import plot_model
from callbacks import ModelCheckpoint


############
#  CHECKS  #
############

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
# to normalize them is to make crops ("make_crops" variable)
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
crop_shape = (256, 256, 1)
# Flag to make crops on the train data
make_crops = True
# Flag to check the crops. Useful to ensure that the crops have been made 
# correctly. Note: if "discard_cropped_images" is True only the run that 
# prepare the discarded data will check the crops, as the future runs only load 
# the crops stored by this first run
check_crop = True 
# Instead of make the crops before the network training, this flag activates
# the option to extract a random crop of each train image during data 
# augmentation (with a crop shape defined by "crop_shape" variable). This flag
# is not compatible with "make_crops" variable
random_crops_in_DA = False 
# NEEDED CODE REFACTORING OF THIS SECTION
test_ov_crops = 8 # Only active with random_crops_in_DA
probability_map = False # Only active with random_crops_in_DA                       
w_foreground = 0.94 # Only active with probability_map
w_background = 0.06 # Only active with probability_map


### Discard variables
# Flag to activate the discards in the main train data. Only active when 
# "make_crops" variable is True
discard_cropped_images = False
# Percentage of pixels labeled with the foreground class necessary to not 
# discard the image 
d_percentage_value = 0.05
# Path where the train discarded data will be stored to be loaded by future runs 
# instead of make again the process
train_crop_discard_path = \
    os.path.join(args.result_dir, 'data_d', job_identifier 
                 + str(d_percentage_value), 'train', 'x')
# Path where the train discarded masks will be stored                           
train_crop_discard_mask_path = \
    os.path.join(args.result_dir, 'data_d', job_identifier 
                 + str(d_percentage_value), 'train', 'y')
# The discards are NOT done in the test data, but this will store the test data,
# which will be cropped, into the pointed path to be loaded by future runs      
# together with the train discarded data and masks                              
test_crop_discard_path = \
    os.path.join(args.result_dir, 'data_d', job_identifier 
                 + str(d_percentage_value), 'test', 'x')
test_crop_discard_mask_path = \
    os.path.join(args.result_dir, 'data_d', job_identifier 
                 + str(d_percentage_value), 'test', 'y')


### Normalization
# Flag to normalize the data dividing by the mean pixel value
normalize_data = False                                                          
# Force the normalization value to the given number instead of the mean pixel 
# value
norm_value_forced = -1                                                          


### Data augmentation (DA) variables
# Flag to decide which type of DA implementation will be used. Select False to 
# use Keras API provided DA, otherwise, a custom implementation will be used
custom_da = False
# Create samples of the DA made. Useful to check the output images made. 
# This option is available for both Keras and custom DA
aug_examples = True 
# Flag to shuffle the training data on every epoch 
#(Best options: Keras->False, Custom->True)
shuffle_train_data_each_epoch = custom_da
# Flag to shuffle the validation data on every epoch
# (Best option: False in both cases)
shuffle_val_data_each_epoch = False
# Make a bit of zoom in the images. Only available in Keras DA
keras_zoom = False 
# width_shift_range (more details in Keras ImageDataGenerator class). Only 
# available in Keras DA
w_shift_r = 0.0
# height_shift_range (more details in Keras ImageDataGenerator class). Only      
# available in Keras DA
h_shift_r = 0.0
# shear_range (more details in Keras ImageDataGenerator class). Only      
# available in Keras DA
shear_range = 0.0 
# Range to pick a brightness value from to apply in the images. Available for 
# both Keras and custom DA. Example of use: brightness_range = [1.0, 1.0]
brightness_range = None 
# Range to pick a median filter size value from to apply in the images. Option
# only available in custom DA
median_filter_size = [0, 0]
# Range of rotation
rotation_range = 180
# Flag to make flips on the subvolumes. Available for both Keras and custom DA.
flips = True


### Extra train data generation
# Number of times to duplicate the train data. Useful when "random_crops_in_DA"
# is made, as more original train data can be cover
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
# Name of the folder where weights files will be stored/loaded from. This folder 
# must be located inside the directory pointed by "args.base_work_dir" variable. 
# If there is no such directory, it will be created for the first time
h5_dir = os.path.join(args.result_dir, 'h5_files')


### Experiment main parameters
# Loss type, three options: "bce" or "w_bce_dice", which refers to binary cross 
# entropy (BCE) and BCE and Dice with with a weight term on each one (that must 
# sum 1) to calculate the total loss value. NOTE: "w_bce" is not implemented on 
# this template type: please use big_data_template.py instead.
loss_type = "bce"
# Batch size value
batch_size_value = 6
# Optimizer to use. Possible values: "sgd" or "adam"
optimizer = "sgd"
# Learning rate used by the optimization method
learning_rate_value = 0.001
# Number of epochs to train the network
epochs_value = 360
# Number of epochs to stop the training process after no improvement
patience = 50 
# Flag to activate the creation of a chart showing the loss and metrics fixing 
# different binarization threshold values, from 0.1 to 1. Useful to check a 
# correct threshold value (normally 0.5)
make_threshold_plots = False
# If weights on data are going to be applied. To true when loss_type is 'w_bce' 
weights_on_data = True if loss_type == "w_bce" else False


### Network architecture specific parameters
# Number of channels in the first initial layer of the network
num_init_channels = 32 
# Flag to activate the Spatial Dropout instead of use the "normal" dropout layer
spatial_dropout = False
# Fixed value to make the dropout. Ignored if the value is zero
fixed_dropout_value = 0.0 


### Post-processing
# Flag to activate the post-processing (Smoooth and Z-filtering)
post_process = True


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
# Directory where predicted images of the segmentation will be stored
result_dir = os.path.join(args.result_dir, 'results', job_identifier)
# Directory where binarized predicted images will be stored
result_bin_dir = os.path.join(result_dir, 'binarized')
# Directory where predicted images will be stored
result_no_bin_dir = os.path.join(result_dir, 'no_binarized')
# Directory where binarized predicted images with 50% of overlap will be stored
result_bin_dir_50ov = os.path.join(result_dir, 'binarized_50ov')
# Directory where predicted images with 50% of overlap will be stored
result_no_bin_dir_50ov = os.path.join(result_dir, 'no_binarized_50ov')
# Folder where the smoothed images will be stored
smooth_dir = os.path.join(result_dir, 'smooth')
# Folder where the images with the z-filter applied will be stored
zfil_dir = os.path.join(result_dir, 'zfil')
# Folder where the images with smoothing and z-filter applied will be stored
smoo_zfil_dir = os.path.join(result_dir, 'smoo_zfil')
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


#####################
#   SANITY CHECKS   #
#####################

print("#####################\n#   SANITY CHECKS   #\n#####################")

check_binary_masks(train_mask_path)
check_binary_masks(test_mask_path)
if extra_datasets_mask_list: 
    for i in range(len(extra_datasets_mask_list)):
        check_binary_masks(extra_datasets_mask_list[i])


#############################################
#    PREPARE DATASET IF DISCARD IS ACTIVE   #
#############################################

# The first time the dataset will be prepared for future runs if it is not 
# created yet
if discard_cropped_images == True and make_crops == True \
   and not os.path.exists(train_crop_discard_path):

    print("##################\n#  DISCARD DATA  #\n##################\n") 

    # Load data
    X_train, Y_train, \
    X_test, Y_test, \
    orig_test_shape, norm_value, \
    crops_made = load_data(
        train_path, train_mask_path, test_path, test_mask_path, img_train_shape, 
        img_test_shape, create_val=False, job_id=args.job_id, crop_shape=crop_shape, 
        check_crop=check_crop, check_crop_path=check_crop_path, 
        d_percentage=d_percentage_value)

    # Create folders and save the images for future runs 
    print("Saving cropped images for future runs . . .")
    save_img(X=X_train, data_dir=train_crop_discard_path, Y=Y_train,            
             mask_dir=train_crop_discard_mask_path)                             
    save_img(X=X_test, data_dir=test_crop_discard_path, Y=Y_test,               
             mask_dir=test_crop_discard_mask_path)

    del X_train, Y_train, X_test, Y_test
   
    # Update shapes 
    img_train_shape = crop_shape
    img_test_shape = crop_shape
    discard_made_run = True
else:
    discard_made_run = False

# Disable the crops if the run is not the one that have prepared the discarded 
# data as it will work with cropped images instead of the original ones, 
# rewriting the needed images 
if discard_cropped_images == True and discard_made_run == False:
    check_crop = False

# For the rest of runs that are not the first that prepares the dataset when 
# discard is active some variables must be set as if it would made the crops
if make_crops == True and discard_cropped_images == True:
    train_path = train_crop_discard_path
    train_mask_path = train_crop_discard_mask_path
    test_path = test_crop_discard_path
    test_mask_path = test_crop_discard_mask_path
    img_train_shape = crop_shape
    img_test_shape = crop_shape
    crops_made = True


##########################                                                      
#       LOAD DATA        #                                                      
##########################

print("##################\n#    LOAD DATA   #\n##################\n")

X_train, Y_train, X_val,\
Y_val, X_test, Y_test,\
orig_test_shape, norm_value, crops_made = load_data(
    train_path, train_mask_path, test_path, test_mask_path, img_train_shape, 
    img_test_shape, val_split=perc_used_as_val, shuffle_val=random_val_data,
    e_d_data=extra_datasets_data_list, e_d_mask=extra_datasets_mask_list, 
    e_d_data_dim=extra_datasets_data_dim_list, e_d_dis=extra_datasets_discard, 
    num_crops_per_dataset=num_crops_per_dataset, make_crops=make_crops, 
    crop_shape=crop_shape, check_crop=check_crop, 
    check_crop_path=check_crop_path)

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
    
# Crop the data to the desired size
if make_crops == True and crops_made == True:
    img_width = crop_shape[0]
    img_height = crop_shape[1]
    img_channels = crop_shape[2]
else:                                                                           
    img_width = img_train_shape[0]
    img_height = img_train_shape[1]                                               
    img_channels = img_train_shape[2]


#############################
#   EXTRA DATA GENERATION   #
#############################

# Duplicate train data N times
if duplicate_train != 0:
    print("##################\n# DUPLICATE DATA #\n##################\n")

    X_train = np.vstack([X_train]*duplicate_train)
    Y_train = np.vstack([Y_train]*duplicate_train)
    print("Train data replicated {} times. Its new shape is: {}"
          .format(duplicate_train, X_train.shape))

# Add extra train data generated with DA
if extra_train_data != 0:
    print("##################\n#   EXTRA DATA   #\n##################\n")

    if custom_da == False:
        # Keras DA generated extra data

        extra_x, extra_y = keras_gen_samples(
            extra_train_data, X_data=X_train, Y_data=Y_train, 
            batch_size_value=batch_size_value, zoom=keras_zoom, 
            w_shift_r=w_shift_r, h_shift_r=h_shift_r, shear_range=shear_range,
            brightness_range=brightness_range, rotation_range=rotation_range,
            hflip=flips, vflip=flips)
    else:
        # Custom DA generated extra data
        extra_gen_args = dict(
            X=X_train, Y=Y_train, batch_size=batch_size_value,
            dim=(img_height,img_width), n_channels=1, shuffle=True, da=True, 
            e_prob=0.0, elastic=False, vflip=flips, hflip=flips, 
            rotation90=False, random_crops_in_DA=random_crops_in_DA, 
            crop_length=crop_shape[0], rotation_range=rotation_range)

        extra_generator = ImageDataGenerator(**extra_gen_args)

        extra_x, extra_y = extra_generator.get_transformed_samples(
            extra_train_data, force_full_images=True)

    X_train = np.vstack((X_train, extra_x*255))
    Y_train = np.vstack((Y_train, extra_y*255))
    print("{} extra train data generated, the new shape of the train now is {}"\
          .format(extra_train_data, X_train.shape))


##########################
#    DATA AUGMENTATION   #
##########################

print("##################\n#    DATA AUG    #\n##################\n")

if custom_da == False:                                                          
    print("Keras DA selected")

    # Keras Data Augmentation                                                   
    train_generator, \
    val_generator = keras_da_generator(
        X_train=X_train, Y_train=Y_train, X_val=X_val, Y_val=Y_val, 
        batch_size_value=batch_size_value, save_examples=aug_examples,
        out_dir=da_samples_dir, shuffle_train=shuffle_train_data_each_epoch, 
        shuffle_val=shuffle_val_data_each_epoch, zoom=keras_zoom, 
        rotation_range=rotation_range, random_crops_in_DA=random_crops_in_DA,
        crop_length=crop_shape[0], w_shift_r=w_shift_r, h_shift_r=h_shift_r,    
        shear_range=shear_range, brightness_range=brightness_range,
        weights_on_data=weights_on_data, weights_path=loss_weight_dir,
        hflip=flips, vflip=flips)
        
else:                                                                           
    print("Custom DA selected")

    # Calculate the probability map per image
    train_prob = None
    if probability_map == True:
        train_prob = np.copy(Y_train[:,:,:,0])
        train_prob = np.float32(train_prob)

        print("Calculating the probability map . . .")
        for i in range(train_prob.shape[0]):
            pdf = train_prob[i]
        
            # Remove artifacts connected to image border
            pdf = clear_border(pdf)

            foreground_pixels = (pdf == 255).sum()
            background_pixels = (pdf == 0).sum()

            pdf[np.where(pdf == 255)] = w_foreground/foreground_pixels
            pdf[np.where(pdf == 0)] = w_background/background_pixels
            pdf /= pdf.sum() # Necessary to get all probs sum 1
            train_prob[i] = pdf

    # Custom Data Augmentation                                                  
    data_gen_args = dict(
        X=X_train, Y=Y_train, batch_size=batch_size_value,     
        dim=(img_height,img_width), n_channels=1,              
        shuffle=shuffle_train_data_each_epoch, da=True, e_prob=0.0, 
        elastic=False, vflip=flips, hflip=flips, rotation90=False, 
        rotation_range=180, brightness_range=brightness_range, 
        median_filter_size=median_filter_size, 
        random_crops_in_DA=random_crops_in_DA, crop_length=crop_shape[0], 
        prob_map=probability_map, train_prob=train_prob)                            
    data_gen_val_args = dict(
        X=X_val, Y=Y_val, batch_size=batch_size_value, 
        dim=(img_height,img_width), n_channels=1, 
        shuffle=shuffle_val_data_each_epoch, da=False, 
        random_crops_in_DA=random_crops_in_DA, crop_length=crop_shape[0], 
        val=True)              
    train_generator = ImageDataGenerator(**data_gen_args)                       
    val_generator = ImageDataGenerator(**data_gen_val_args)                     
                                                                                
    # Generate examples of data augmentation                                    
    if aug_examples == True:                                                    
        train_generator.get_transformed_samples(
            10, save_to_dir=True, train=False, out_dir=da_samples_dir)
                                                                                
if random_crops_in_DA == True:
    img_width = crop_shape[0]
    img_height = crop_shape[1]


##########################
#    BUILD THE NETWORK   #
##########################

print("###################\n#  TRAIN PROCESS  #\n###################\n")

print("Creating the network . . .")
model = U_Net([img_height, img_width, img_channels], 
              numInitChannels=num_init_channels, 
              fixed_dropout=fixed_dropout_value, spatial_dropout=spatial_dropout,
              loss_type=loss_type, optimizer=optimizer, lr=learning_rate_value,
              fine_tunning=fine_tunning)

# Check the network created
model.summary(line_length=150)
os.makedirs(char_dir, exist_ok=True)
model_name = os.path.join(char_dir, "model_plot_" + job_identifier + ".png")
plot_model(model, to_file=model_name, show_shapes=True, show_layer_names=True)

if load_previous_weights == False:
    if fine_tunning == True:                                                    
        h5_file=os.path.join(h5_dir, weight_files_prefix + fine_tunning_weigths 
                             + '_' + args.run_id + '.h5')     
        print("Fine-tunning: loading model weights from h5_file: {}"\
              .format(h5_file))
        model.load_weights(h5_file)                                             
   
    results = model.fit(x=train_generator, validation_data=val_generator,
        validation_steps=math.ceil(X_val.shape[0]/batch_size_value),
        steps_per_epoch=math.ceil(X_train.shape[0]/batch_size_value),
        epochs=epochs_value, callbacks=[earlystopper, checkpointer, time_callback])
else:
    h5_file=os.path.join(h5_dir, weight_files_prefix + previous_job_weights 
                         + '_' + str(args.run_id) + '.h5')
    print("Loading model weights from h5_file: {}".format(h5_file))
    model.load_weights(h5_file)


#####################
#     INFERENCE     #
#####################

print("##################\n#    INFERENCE   #\n##################\n")

# Divide the test data to fit into crop shape used to train
if random_crops_in_DA == True:
    X_test, Y_test = crop_data_with_overlap(
        X_test, Y_test, crop_shape[0], test_ov_crops)

Y_test /= 255 if np.max(Y_test) > 1 else Y_test
X_test /= 255 if np.max(X_test) > 1 else X_test

print("Evaluating test data . . .")
score = model.evaluate(X_test, Y_test, batch_size=batch_size_value, verbose=1)
jac_per_crop = score[1]

print("Making the predictions on test data . . .")
preds_test = model.predict(X_test, batch_size=batch_size_value, verbose=1)

# Reconstruct the data to the original shape
if make_crops == True:
    h_num = math.ceil(orig_test_shape[1]/preds_test.shape[1])
    v_num = math.ceil(orig_test_shape[2]/preds_test.shape[2]) 

    print("Reconstruct X_test . . .")    
    X_test = merge_data_without_overlap(
        X_test, math.ceil(X_test.shape[0]/(h_num*v_num)),
        out_shape=[h_num, v_num], grid=False)
    print("Reconstruct Y_test . . .")
    Y_test = merge_data_without_overlap(
        Y_test, math.ceil(Y_test.shape[0]/(h_num*v_num)),
        out_shape=[h_num, v_num], grid=False)
    
    print("Reconstruct preds_test . . .")
    preds_test = merge_data_without_overlap(
        preds_test, math.ceil(preds_test.shape[0]/(h_num*v_num)),
        out_shape=[h_num, v_num], grid=False)

elif random_crops_in_DA == True and test_ov_crops > 1:
    print("Reconstruct preds_test . . .")
    preds_test = merge_data_with_overlap(
        preds_test, orig_test_shape, crop_shape[0], test_ov_crops, result_dir)

print("Saving predicted images . . .")
save_img(Y=(preds_test > 0.5).astype(np.uint8), 
         mask_dir=result_bin_dir, prefix="test_out_bin")
save_img(Y=preds_test, mask_dir=result_no_bin_dir, prefix="test_out_no_bin")

# Generate a plot with different binarization thresholds 
if make_threshold_plots == True and random_crops_in_DA == False:
    print("Calculate metrics with different thresholds . . .")
    score[1], voc, det = threshold_plots(
        preds_test, Y_test, orig_test_shape, score, det_eval_ge_path, 
        det_eval_path, det_bin, n_dig, args.job_id, job_identifier, char_dir)

print("Calculate metrics . . .")
score[1] = jaccard_index_numpy(Y_test, (preds_test > 0.5).astype(np.uint8))
voc = voc_calculation(Y_test, (preds_test > 0.5).astype(np.uint8), score[1])
det = DET_calculation(Y_test, (preds_test > 0.5).astype(np.uint8), 
                      det_eval_ge_path, det_eval_path, det_bin, n_dig, args.job_id)
if make_crops == True or random_crops_in_DA == True:
    Y_test_50ov = np.zeros(X_test.shape, dtype=(np.float32))
    for i in tqdm(range(X_test.shape[0])):
        predictions_smooth = predict_img_with_overlap(
            X_test[i,:,:,:], window_size=crop_shape[0], subdivisions=2, 
            nb_classes=1, pred_func=(
                lambda img_batch_subdiv: model.predict(img_batch_subdiv)))
        Y_test_50ov[i] = predictions_smooth

    print("Saving 50% overlap predicted images . . .")
    save_img(Y=(Y_test_50ov > 0.5).astype(np.float32), 
             mask_dir=result_bin_dir_50ov, prefix="test_out_bin_50ov")
    save_img(Y=Y_test_50ov, mask_dir=result_no_bin_dir_50ov,
             prefix="test_out_no_bin_50ov")

    print("Calculate metrics for 50% overlap images . . .")
    jac_per_img_50ov = jaccard_index_numpy(
        Y_test, (Y_test_50ov > 0.5).astype(np.float32))
    voc_per_img_50ov = voc_calculation(
        Y_test, (Y_test_50ov > 0.5).astype(np.float32), jac_per_img_50ov)
    det_per_img_50ov = DET_calculation(
        Y_test, (Y_test_50ov > 0.5).astype(np.float32), det_eval_ge_path, 
        det_eval_path, det_bin, n_dig, args.job_id)
    
    del Y_test_50ov
else:
    jac_per_img_50ov = -1
    voc_per_img_50ov = -1
    det_per_img_50ov = -1


####################
#  POST-PROCESING  #
####################

if (post_process == True and make_crops == True) or (random_crops_in_DA == True):

    print("##################\n# POST-PROCESING #\n##################\n")

    print("1) SMOOTH")

    Y_test_smooth = np.zeros(X_test.shape, dtype=(np.uint8))

    # Extract the number of digits to create the image names
    d = len(str(X_test.shape[0]))

    os.makedirs(smooth_dir, exist_ok=True)

    print("Smoothing crops . . .")
    for i in tqdm(range(X_test.shape[0])):
        predictions_smooth = predict_img_with_smooth_windowing(
            X_test[i,:,:,:], window_size=crop_shape[0], subdivisions=2,  
            nb_classes=1, pred_func=(
                lambda img_batch_subdiv: model.predict(img_batch_subdiv)))

        Y_test_smooth[i] = (predictions_smooth > 0.5).astype(np.uint8)

        im = Image.fromarray(predictions_smooth[:,:,0]*255)
        im = im.convert('L')
        im.save(os.path.join(smooth_dir,"test_out_smooth_" + str(i).zfill(d) 
                                        + ".png"))

    # Metrics (Jaccard + VOC + DET)
    print("Calculate metrics . . .")
    smooth_score = jaccard_index_numpy(Y_test, Y_test_smooth)
    smooth_voc = voc_calculation(Y_test, Y_test_smooth, smooth_score)
    smooth_det = DET_calculation(Y_test, Y_test_smooth, det_eval_ge_path,
                                 det_eval_post_path, det_bin, n_dig, args.job_id)

zfil_preds_test = None
smooth_zfil_preds_test = None
if post_process == True and not extra_datasets_data_list:
    print("2) Z-FILTERING")

    if random_crops_in_DA == False:
        print("Applying Z-filter . . .")
        zfil_preds_test = calculate_z_filtering((preds_test > 0.5).astype(np.uint8))
    else:
        if test_ov_crops > 1:
            print("Applying Z-filter . . .")
            zfil_preds_test = calculate_z_filtering(merged_preds_test)

    if zfil_preds_test is not None:
        print("Saving Z-filtered images . . .")
        save_img(Y=zfil_preds_test, mask_dir=zfil_dir, prefix="test_out_zfil")
 
        print("Calculate metrics for the Z-filtered data . . .")
        zfil_score = jaccard_index_numpy(Y_test, zfil_preds_test)
        zfil_voc = voc_calculation(Y_test, zfil_preds_test, zfil_score)
        zfil_det = DET_calculation(Y_test, zfil_preds_test, det_eval_ge_path,
                                   det_eval_post_path, det_bin, n_dig, 
                                   args.job_id)

    if Y_test_smooth is not None:
        print("Applying Z-filter to the smoothed data . . .")
        smooth_zfil_preds_test = calculate_z_filtering(Y_test_smooth)

        print("Saving smoothed + Z-filtered images . . .")
        save_img(Y=smooth_zfil_preds_test, mask_dir=smoo_zfil_dir, 
                 prefix="test_out_smoo_zfil")

        print("Calculate metrics for the smoothed + Z-filtered data . . .")
        smo_zfil_score = jaccard_index_numpy(Y_test, smooth_zfil_preds_test)
        smo_zfil_voc = voc_calculation(
            Y_test, smooth_zfil_preds_test, smo_zfil_score)
        smo_zfil_det = DET_calculation(
                Y_test, smooth_zfil_preds_test, det_eval_ge_path, 
                det_eval_post_path, det_bin, n_dig, args.job_id)

print("Finish post-processing") 


####################################
#  PRINT AND SAVE SCORES OBTAINED  #
####################################

if load_previous_weights == False:
    print("Epoch average time: {}".format(np.mean(time_callback.times)))
    print("Epoch number: {}".format(len(results.history['val_loss'])))
    print("Train time (s): {}".format(np.sum(time_callback.times)))
    print("Train loss: {}".format(np.min(results.history['loss'])))
    print("Train jaccard_index: {}"
          .format(np.max(results.history['jaccard_index'])))
    print("Validation loss: {}".format(np.min(results.history['val_loss'])))
    print("Validation jaccard_index: {}"
          .format(np.max(results.history['val_jaccard_index'])))

print("Test loss: {}".format(score[0]))
print("Test jaccard_index (per crop): {}".format(jac_per_crop))
print("Test jaccard_index (per image): {}".format(score[1]))
print("Test jaccard_index (per image with 50% overlap): {}"
      .format(jac_per_img_50ov))
print("VOC (per image): {}".format(voc))
print("VOC (per image with 50% overlap): {}".format(voc_per_img_50ov))
print("DET (per image): {}".format(det))
print("DET (per image with 50% overlap): {}".format(det_per_img_50ov))
    
if load_previous_weights == False:
    smooth_score = -1 if 'smooth_score' not in globals() else smooth_score
    smooth_voc = -1 if 'smooth_voc' not in globals() else smooth_voc
    smooth_det = -1 if 'smooth_det' not in globals() else smooth_det
    zfil_score = -1 if 'zfil_score' not in globals() else zfil_score
    zfil_voc = -1 if 'zfil_voc' not in globals() else zfil_voc
    zfil_det = -1 if 'zfil_det' not in globals() else zfil_det
    smo_zfil_score = -1 if 'smo_zfil_score' not in globals() else smo_zfil_score
    smo_zfil_voc = -1 if 'smo_zfil_voc' not in globals() else smo_zfil_voc
    smo_zfil_det = -1 if 'smo_zfil_det' not in globals() else smo_zfil_det
    jac_per_crop = -1 if 'jac_per_crop' not in globals() else jac_per_crop

    store_history(
        results, jac_per_crop, score, jac_per_img_50ov, voc, voc_per_img_50ov, 
        det, det_per_img_50ov, time_callback, result_dir, job_identifier, 
        smooth_score, smooth_voc, smooth_det, zfil_score, zfil_voc, zfil_det, 
        smo_zfil_score, smo_zfil_voc, smo_zfil_det)

    create_plots(results, job_identifier, char_dir)

if (post_process == True and make_crops == True) or (random_crops_in_DA == True):
    print("Post-process: SMOOTH - Test jaccard_index: {}".format(smooth_score))
    print("Post-process: SMOOTH - VOC: {}".format(smooth_voc))
    print("Post-process: SMOOTH - DET: {}".format(smooth_det))

if post_process == True and zfil_preds_test is not None:
    print("Post-process: Z-filtering - Test jaccard_index: {}".format(zfil_score))
    print("Post-process: Z-filtering - VOC: {}".format(zfil_voc))
    print("Post-process: Z-filtering - DET: {}".format(zfil_det))

if post_process == True and smooth_zfil_preds_test is not None:
    print("Post-process: SMOOTH + Z-filtering - Test jaccard_index: {}"
          .format(smo_zfil_score))
    print("Post-process: SMOOTH + Z-filtering - VOC: {}".format(smo_zfil_voc))
    print("Post-process: SMOOTH + Z-filtering - DET: {}".format(smo_zfil_det))

print("FINISHED JOB {} !!".format(job_identifier))
