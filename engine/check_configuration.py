import os

from utils.util import check_value

def check_configuration(cfg):
    """
    Check if the configuration is good. 
    """

    dim_count = 2 if cfg.PROBLEM.NDIM == '2D' else 3

    # Adjust overlap and padding in the default setting if it was not set
    opts = []
    if cfg.PROBLEM.NDIM == '3D':
        if cfg.DATA.TRAIN.OVERLAP == (0,0):
            opts.extend(['DATA.TRAIN.OVERLAP', (0,0,0)])
        if cfg.DATA.TRAIN.PADDING == (0,0):
            opts.extend(['DATA.TRAIN.PADDING', (0,0,0)])
        if cfg.DATA.VAL.OVERLAP == (0,0):
            opts.extend(['DATA.VAL.OVERLAP', (0,0,0)])
        if cfg.DATA.VAL.PADDING == (0,0):
            opts.extend(['DATA.VAL.PADDING', (0,0,0)])
        if cfg.DATA.TEST.OVERLAP == (0,0):
            opts.extend(['DATA.TEST.OVERLAP', (0,0,0)])
        if cfg.DATA.TEST.PADDING == (0,0):
            opts.extend(['DATA.TEST.PADDING', (0,0,0)])

    # Adjust channel weights 
    if cfg.PROBLEM.TYPE == 'INSTANCE_SEG':
        if len(cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNEL_WEIGHTS) != len(str(cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNELS)):
            if cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNEL_WEIGHTS == (1, 1):
                opts.extend(['cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNEL_WEIGHTS', (1,1,1)])    
        
    # Adjust dropout to feature maps
    if len(cfg.MODEL.FEATURE_MAPS) != len(cfg.MODEL.DROPOUT_VALUES):
        if all(x == 0 for x in cfg.MODEL.DROPOUT_VALUES):
            opts.extend(['MODEL.DROPOUT_VALUES', (0.,)*len(cfg.MODEL.FEATURE_MAPS)])
        else:
            raise ValueError("'MODEL.FEATURE_MAPS' and 'MODEL.DROPOUT_VALUES' lengths must be equal")

    if len(cfg.DATA.TRAIN.RESOLUTION) == 1 and cfg.DATA.TRAIN.RESOLUTION[0] == -1:
        opts.extend(['DATA.TRAIN.RESOLUTION', (1,)*dim_count])
    if len(cfg.DATA.VAL.RESOLUTION) == 1 and cfg.DATA.VAL.RESOLUTION[0] == -1:
        opts.extend(['DATA.VAL.RESOLUTION', (1,)*dim_count])
    if len(cfg.DATA.TEST.RESOLUTION) == 1 and cfg.DATA.TEST.RESOLUTION[0] == -1:
        opts.extend(['DATA.TEST.RESOLUTION', (1,)*dim_count])

    if len(opts) > 0:
        cfg.merge_from_list(opts)

    #### General checks ####
    assert cfg.PROBLEM.NDIM in ['2D', '3D']
    assert cfg.PROBLEM.TYPE in ['SEMANTIC_SEG', 'INSTANCE_SEG', 'CLASSIFICATION', 'DETECTION', 'DENOISING', 'SUPER_RESOLUTION', 'SELF_SUPERVISED']

    if not cfg.TEST.STATS.PER_PATCH and not cfg.TEST.STATS.FULL_IMG:
        raise ValueError("One between 'TEST.STATS.PER_PATCH' or 'TEST.STATS.FULL_IMG' need to be True")

    if cfg.PROBLEM.NDIM == '3D' and not cfg.TEST.STATS.PER_PATCH and not cfg.TEST.STATS.MERGE_PATCHES:
        raise ValueError("One between 'TEST.STATS.PER_PATCH' or 'TEST.STATS.MERGE_PATCHES' need to be True when 'PROBLEM.NDIM'=='3D'")

    if cfg.TEST.MAP and not os.path.isdir(cfg.PATHS.MAP_CODE_DIR):
        raise ValueError("mAP calculation code not found. Please set 'PATHS.MAP_CODE_DIR' variable with the path of the "
                         "Github repo 'mAP_3Dvolume': 0) git clone https://github.com/danifranco/mAP_3Dvolume.git ; "
                         "1) git checkout grand-challenge ")

    if cfg.PROBLEM.NDIM == '3D' and cfg.TEST.STATS.FULL_IMG:
        print("WARNING: TEST.STATS.FULL_IMG == True while using PROBLEM.NDIM == '3D'. As 3D images are usually 'huge'"
              ", full image statistics will be disabled to avoid GPU memory overflow")

    if cfg.LOSS.TYPE != "CE" and cfg.PROBLEM.TYPE not in ['SEMANTIC_SEG', 'DETECTION']:
        raise ValueError("Not implemented pipeline option: LOSS.TYPE != 'CE' only available in 'SEMANTIC_SEG' and 'DETECTION'")

    #### Semantic segmentation ####
    if cfg.PROBLEM.TYPE == 'SEMANTIC_SEG':
        if cfg.MODEL.N_CLASSES == 0:
            raise ValueError("'MODEL.N_CLASSES' can not be 0")
        if cfg.LOSS.TYPE == "MASKED_BCE":
            if cfg.MODEL.N_CLASSES > 1:
                raise ValueError("Not implemented pipeline option: N_CLASSES > 1 and MASKED_BCE")
                    
    #### Instance segmentation ####
    if cfg.PROBLEM.TYPE == 'INSTANCE_SEG':
        assert cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNELS in ['BC', 'BCM', 'BCD', 'BCDv2', 'Dv2', 'BDv2']
        if cfg.MODEL.N_CLASSES > 1:
            raise ValueError("Not implemented pipeline option for INSTANCE_SEGMENTATION")
        if len(cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNEL_WEIGHTS) != len(str(cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNELS)):
            raise ValueError("'PROBLEM.INSTANCE_SEG.DATA_CHANNEL_WEIGHTS' needs to be of the same length as the channels selected in 'PROBLEM.INSTANCE_SEG.DATA_CHANNELS'. "
                            "E.g. 'PROBLEM.INSTANCE_SEG.DATA_CHANNELS'='BC' 'PROBLEM.INSTANCE_SEG.DATA_CHANNEL_WEIGHTS'=[1,0.5]. "
                            "'PROBLEM.INSTANCE_SEG.DATA_CHANNELS'='BCD' 'PROBLEM.INSTANCE_SEG.DATA_CHANNEL_WEIGHTS'=[0.5,0.5,1]")
        if cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNELS not in ['BC', 'BCM', 'BCD', 'BCDv2'] and cfg.TEST.POST_PROCESSING.VORONOI_ON_MASK:
            raise ValueError("'PROBLEM.INSTANCE_SEG.DATA_CHANNELS' need to be one between ['BC', 'BCM', 'BCD', 'BCDv2'] "
                             "when 'TEST.POST_PROCESSING.VORONOI_ON_MAS' is enabled")

    #### Detection ####
    if cfg.PROBLEM.TYPE == 'DETECTION':
        if cfg.MODEL.N_CLASSES == 0:
            raise ValueError("'MODEL.N_CLASSES' can not be 0")

    #### Super-resolution ####
    elif cfg.PROBLEM.TYPE == 'SUPER_RESOLUTION':
        if not cfg.DATA.EXTRACT_RANDOM_PATCH:
            raise ValueError("'DATA.EXTRACT_RANDOM_PATCH' need to be True for 'SUPER_RESOLUTION'")
        if cfg.AUGMENTOR.RANDOM_CROP_SCALE == 1:
            raise ValueError("Resolution scale must be provided with 'AUGMENTOR.RANDOM_CROP_SCALE' variable")
        if cfg.PROBLEM.NDIM == '3D':
            raise NotImplementedError

    #### Self-supervision ####
    elif cfg.PROBLEM.TYPE == 'SELF_SUPERVISED':
        if cfg.PROBLEM.SELF_SUPERVISED.RESIZING_FACTOR not in [2,4,6]:
            raise ValueError("PROBLEM.SELF_SUPERVISED.RESIZING_FACTOR not in [2,4,6]")
        if not check_value(cfg.PROBLEM.SELF_SUPERVISED.NOISE):
            raise ValueError("PROBLEM.SELF_SUPERVISED.NOISE not in [0, 1] range")

    #### Denoising ####
    elif cfg.PROBLEM.TYPE == 'DENOISING':
        if cfg.DATA.TEST.IN_MEMORY:
            raise ValueError("DATA.TEST.IN_MEMORY==True not supported in DENOISING. Please change it to False")
        if cfg.DATA.TEST.LOAD_GT:
            raise NotImplementedError

    ### Pre-processing ###
    if cfg.DATA.EXTRACT_RANDOM_PATCH and cfg.DATA.PROBABILITY_MAP:
        if cfg.DATA.W_FOREGROUND+cfg.DATA.W_BACKGROUND != 1:
            raise ValueError("cfg.DATA.W_FOREGROUND+cfg.DATA.W_BACKGROUND need to sum 1. E.g. 0.94 and 0.06 respectively.")

    #### Data #### 
    if cfg.TRAIN.ENABLE:
        if cfg.PROBLEM.TYPE in ['SEMANTIC_SEG', 'INSTANCE_SEG', 'DETECTION', 'DENOISING', 'SUPER_RESOLUTION', 'SELF_SUPERVISED']:
            if not cfg.DATA.TRAIN.IN_MEMORY:
                if not os.path.exists(cfg.DATA.TRAIN.PATH):
                    raise ValueError("Train data dir not found: {}".format(cfg.DATA.TRAIN.PATH))
                if not os.path.exists(cfg.DATA.TRAIN.MASK_PATH) and cfg.PROBLEM.TYPE != 'DENOISING':
                    raise ValueError("Train mask data dir not found: {}".format(cfg.DATA.TRAIN.MASK_PATH))
            if not cfg.DATA.VAL.FROM_TRAIN and not cfg.DATA.VAL.IN_MEMORY:
                if not os.path.exists(cfg.DATA.TRAIN.PATH):
                    raise ValueError("Train data dir not found: {}".format(cfg.DATA.TRAIN.PATH))
                if not os.path.exists(cfg.DATA.TRAIN.MASK_PATH) and cfg.PROBLEM.TYPE != 'DENOISING':
                    raise ValueError("Train mask data dir not found: {}".format(cfg.DATA.TRAIN.MASK_PATH))
        else:
            if not os.path.exists(cfg.DATA.TRAIN.PATH):
                raise ValueError("Train data dir not found: {}".format(cfg.DATA.TRAIN.PATH))
            if not os.path.exists(cfg.DATA.VAL.PATH):
                raise ValueError("Validation data dir not found: {}".format(cfg.DATA.VAL.PATH))
    if cfg.TEST.ENABLE:
        if not os.path.exists(cfg.DATA.TEST.PATH):
            raise ValueError("Test data not found: {}".format(cfg.DATA.TEST.PATH))
        if cfg.DATA.TEST.LOAD_GT and not os.path.exists(cfg.DATA.TEST.MASK_PATH):
            raise ValueError("Test data mask not found: {}".format(cfg.DATA.TEST.MASK_PATH))

    if not cfg.DATA.TRAIN.IN_MEMORY:
        if not os.path.exists(cfg.DATA.TRAIN.PATH):
            raise ValueError("Train data dir not found: {}".format(cfg.DATA.TRAIN.PATH))
        if not os.path.exists(cfg.DATA.TRAIN.MASK_PATH) and cfg.PROBLEM.TYPE != 'DENOISING':
            raise ValueError("Train mask data dir not found: {}".format(cfg.DATA.TRAIN.MASK_PATH))
        if cfg.DATA.EXTRACT_RANDOM_PATCH and cfg.DATA.PROBABILITY_MAP:
            if not cfg.PROBLEM.TYPE == 'SEMANTIC_SEG':
                raise ValueError("'DATA.PROBABILITY_MAP' can only be selected when 'PROBLEM.TYPE' is 'SEMANTIC_SEG'")

    if cfg.DATA.VAL.FROM_TRAIN and not cfg.DATA.VAL.CROSS_VAL and cfg.DATA.VAL.SPLIT_TRAIN <= 0:
        raise ValueError("'DATA.VAL.SPLIT_TRAIN' needs to be > 0 when 'DATA.VAL.FROM_TRAIN' == True")
    if cfg.DATA.VAL.FROM_TRAIN and not cfg.DATA.TRAIN.IN_MEMORY:
        raise ValueError("Validation can be extracted from train while 'DATA.TRAIN.IN_MEMORY' == False. Please set"
                         "'DATA.VAL.FROM_TRAIN' to False")
                        
    if len(cfg.DATA.TRAIN.RESOLUTION) != dim_count:
        raise ValueError("Train resolution needs to be a tuple with {} values".format(dim_count))
    if len(cfg.DATA.VAL.RESOLUTION) != dim_count:
        raise ValueError("Validation resolution needs to be a tuple with {} values".format(dim_count))
    if len(cfg.DATA.TEST.RESOLUTION) != dim_count:
        raise ValueError("Test resolution needs to be a tuple with {} values".format(dim_count))

    if len(cfg.DATA.TRAIN.OVERLAP) != dim_count:
        raise ValueError("When PROBLEM.NDIM == {} DATA.TRAIN.OVERLAP tuple must be lenght {}, given {}."
                         .format(cfg.PROBLEM.NDIM, dim_count, cfg.DATA.TRAIN.OVERLAP))
    if len(cfg.DATA.TRAIN.PADDING) != dim_count:
        raise ValueError("When PROBLEM.NDIM == {} DATA.TRAIN.PADDING tuple must be lenght {}, given {}."
                         .format(cfg.PROBLEM.NDIM, dim_count, cfg.DATA.TRAIN.PADDING))
    if len(cfg.DATA.TEST.OVERLAP) != dim_count:
        raise ValueError("When PROBLEM.NDIM == {} DATA.TEST.OVERLAP tuple must be lenght {}, given {}."
                         .format(cfg.PROBLEM.NDIM, dim_count, cfg.DATA.TEST.OVERLAP))
    if len(cfg.DATA.TEST.PADDING) != dim_count:
        raise ValueError("When PROBLEM.NDIM == {} DATA.TEST.PADDING tuple must be lenght {}, given {}."
                         .format(cfg.PROBLEM.NDIM, dim_count, cfg.DATA.TEST.PADDING))
    if len(cfg.DATA.PATCH_SIZE) != dim_count+1:
        raise ValueError("When PROBLEM.NDIM == {} DATA.PATCH_SIZE tuple must be lenght {}, given {}."
                         .format(cfg.PROBLEM.NDIM, dim_count+1, cfg.DATA.PATCH_SIZE))
    if len(cfg.DATA.TRAIN.RESOLUTION) != 1 and len(cfg.DATA.TRAIN.RESOLUTION) != dim_count:
        raise ValueError("When PROBLEM.NDIM == {} DATA.TRAIN.RESOLUTION tuple must be lenght {}, given {}."
                         .format(cfg.PROBLEM.NDIM, dim_count, cfg.DATA.TRAIN.RESOLUTION))
    if len(cfg.DATA.VAL.RESOLUTION) != 1 and len(cfg.DATA.VAL.RESOLUTION) != dim_count:
        raise ValueError("When PROBLEM.NDIM == {} DATA.VAL.RESOLUTION tuple must be lenght {}, given {}."
                         .format(cfg.PROBLEM.NDIM, dim_count, cfg.DATA.VAL.RESOLUTION))
    if len(cfg.DATA.TEST.RESOLUTION) != 1 and len(cfg.DATA.TEST.RESOLUTION) != dim_count:
        raise ValueError("When PROBLEM.NDIM == {} DATA.TEST.RESOLUTION tuple must be lenght {}, given {}."
                         .format(cfg.PROBLEM.NDIM, dim_count, cfg.DATA.TEST.RESOLUTION))
    assert cfg.DATA.NORMALIZATION.TYPE in ['div', 'custom']
    if cfg.DATA.NORMALIZATION.TYPE == 'custom':
        if cfg.DATA.NORMALIZATION.CUSTOM_MEAN == -1 and cfg.DATA.NORMALIZATION.CUSTOM_STD == -1:
            if not os.path.exists(cfg.PATHS.MEAN_INFO_FILE) or not os.path.exists(cfg.PATHS.STD_INFO_FILE):
                if not cfg.DATA.TRAIN.IN_MEMORY:
                    raise ValueError("If no 'DATA.NORMALIZATION.CUSTOM_MEAN' and 'DATA.NORMALIZATION.CUSTOM_STD' were provided "
                        "when DATA.NORMALIZATION.TYPE == 'custom', DATA.TRAIN.IN_MEMORY need to be True")

    ### Model ###
    assert cfg.MODEL.ARCHITECTURE in ['unet', 'resunet', 'attention_unet', 'fcn32', 'fcn8', 'tiramisu', 'mnet',
                                      'multiresunet', 'seunet', 'simple_cnn', 'EfficientNetB0', 'unetr', 'edsr']
    if cfg.MODEL.ARCHITECTURE not in ['unet', 'resunet', 'seunet', 'attention_unet'] and cfg.PROBLEM.NDIM == '3D':
        raise ValueError("For 3D these models are available: {}".format(['unet', 'resunet', 'seunet', 'attention_unet']))
    if cfg.MODEL.N_CLASSES > 1 and cfg.MODEL.ARCHITECTURE not in ['unet', 'resunet', 'seunet', 'attention_unet']:
        raise ValueError("'MODEL.N_CLASSES' > 1 can only be used with 'MODEL.ARCHITECTURE' in ['unet', 'resunet', 'seunet', 'attention_unet']")
    if cfg.MODEL.LAST_ACTIVATION not in ['softmax', 'sigmoid', 'linear']:
        raise ValueError("'MODEL.LAST_ACTIVATION' need to be in ['softmax','sigmoid','linear']. Provided {}"
                         .format(cfg.MODEL.LAST_ACTIVATION))
    if cfg.MODEL.UPSAMPLE_LAYER.lower() not in ["upsampling", "convtranspose"]:
        raise ValueError("cfg.MODEL.UPSAMPLE_LAYER' need to be one between ['upsampling', 'convtranspose']. Provided {}"
                          .format(cfg.MODEL.UPSAMPLE_LAYER))
    if cfg.PROBLEM.TYPE == "SEMANTIC_SEG" and cfg.MODEL.ARCHITECTURE not in ['unet', 'resunet', 'attention_unet', 'fcn32', \
        'fcn8', 'tiramisu', 'mnet', 'multiresunet', 'seunet', 'unetr']:
        raise ValueError("Not implemented pipeline option: semantic segmentation models are ['unet', 'resunet', "
                         "'attention_unet', 'fcn32', 'fcn8', 'tiramisu', 'mnet', 'multiresunet', 'seunet', 'unetr']")
    if cfg.PROBLEM.TYPE == "INSTANCE_SEG" and cfg.MODEL.ARCHITECTURE not in ['unet', 'resunet']:
        raise ValueError("Not implemented pipeline option: instance segmentation models are ['unet', 'resunet']")    
    if cfg.PROBLEM.TYPE in ['DETECTION', 'DENOISING', 'SELF_SUPERVISED'] and \
        cfg.MODEL.ARCHITECTURE not in ['unet', 'resunet', 'seunet', 'attention_unet']:
        raise ValueError("Architectures available for {} are: ['unet', 'resunet', 'seunet', 'attention_unet']"
                         .format(cfg.PROBLEM.TYPE))
    if cfg.PROBLEM.TYPE == 'SUPER_RESOLUTION' and cfg.MODEL.ARCHITECTURE not in ['edsr']:
        raise ValueError("Architectures available for 'SUPER_RESOLUTION' are: ['edsr']")
    if cfg.PROBLEM.TYPE == 'CLASSIFICATION' and cfg.MODEL.ARCHITECTURE not in ['simple_cnn', 'EfficientNetB0']:
        raise ValueError("Architectures available for 'CLASSIFICATION' are: ['simple_cnn', 'EfficientNetB0']")

    ### Train ###
    assert cfg.TRAIN.OPTIMIZER in ['SGD', 'ADAM']
    assert cfg.LOSS.TYPE in ['CE', 'W_CE_DICE', 'MASKED_BCE']

    #### Augmentation ####
    if cfg.AUGMENTOR.ENABLE:
        if not check_value(cfg.AUGMENTOR.DA_PROB):
            raise ValueError("AUGMENTOR.DA_PROB not in [0, 1] range")
        if cfg.AUGMENTOR.RANDOM_ROT:
            if not check_value(cfg.AUGMENTOR.RANDOM_ROT_RANGE, (-360,360)):
                raise ValueError("AUGMENTOR.RANDOM_ROT_RANGE values need to be between [-360,360]")
        if cfg.AUGMENTOR.SHEAR:
            if not check_value(cfg.AUGMENTOR.SHEAR_RANGE, (-360,360)):
                raise ValueError("AUGMENTOR.SHEAR_RANGE values need to be between [-360,360]")
        if cfg.AUGMENTOR.ELASTIC:
            if cfg.AUGMENTOR.E_MODE not in ['constant', 'nearest', 'reflect', 'wrap']:
                raise ValueError("AUGMENTOR.E_MODE not in ['constant', 'nearest', 'reflect', 'wrap']")
        if cfg.AUGMENTOR.BRIGHTNESS: 
            if cfg.AUGMENTOR.BRIGHTNESS_MODE not in ['2D', '3D'] and cfg.PROBLEM.NDIM == "3D":
                raise ValueError("AUGMENTOR.BRIGHTNESS_MODE not in ['2D', '3D']")
        if cfg.AUGMENTOR.CONTRAST:
            if cfg.AUGMENTOR.CONTRAST_MODE not in ['2D', '3D'] and cfg.PROBLEM.NDIM == "3D":
                raise ValueError("AUGMENTOR.CONTRAST_MODE not in ['2D', '3D']")
        if cfg.AUGMENTOR.BRIGHTNESS_EM:
            if cfg.AUGMENTOR.BRIGHTNESS_EM_MODE not in ['2D', '3D'] and cfg.PROBLEM.NDIM == "3D":
                raise ValueError("AUGMENTOR.BRIGHTNESS_EM_MODE not in ['2D', '3D']")
        if cfg.AUGMENTOR.CONTRAST_EM:
            if cfg.AUGMENTOR.CONTRAST_EM_MODE not in ['2D', '3D'] and cfg.PROBLEM.NDIM == "3D":
                raise ValueError("AUGMENTOR.CONTRAST_EM_MODE not in ['2D', '3D']")
        if cfg.AUGMENTOR.DROPOUT:
            if not check_value(cfg.AUGMENTOR.DROP_RANGE):
                raise ValueError("AUGMENTOR.DROP_RANGE values not in [0, 1] range")
        if cfg.AUGMENTOR.CUTOUT:
            if not check_value(cfg.AUGMENTOR.COUT_SIZE):
                raise ValueError("AUGMENTOR.COUT_SIZE values not in [0, 1] range")
        if cfg.AUGMENTOR.CUTBLUR:
            if not check_value(cfg.AUGMENTOR.CBLUR_SIZE):
                raise ValueError("AUGMENTOR.CBLUR_SIZE values not in [0, 1] range")
            if not check_value(cfg.AUGMENTOR.CBLUR_DOWN_RANGE, (1,8)):
                raise ValueError("AUGMENTOR.CBLUR_DOWN_RANGE values not in [1, 8] range")
        if cfg.AUGMENTOR.CUTMIX:
            if not check_value(cfg.AUGMENTOR.CMIX_SIZE):
                raise ValueError("AUGMENTOR.CMIX_SIZE values not in [0, 1] range")
        if cfg.AUGMENTOR.CUTNOISE:
            if not check_value(cfg.AUGMENTOR.CNOISE_SCALE):
                raise ValueError("AUGMENTOR.CNOISE_SCALE values not in [0, 1] range")
            if not check_value(cfg.AUGMENTOR.CNOISE_SIZE):
                raise ValueError("AUGMENTOR.CNOISE_SIZE values not in [0, 1] range")
        if cfg.AUGMENTOR.GRIDMASK:
            if not check_value(cfg.AUGMENTOR.GRID_RATIO):
                raise ValueError("AUGMENTOR.GRID_RATIO not in [0, 1] range")
            if cfg.AUGMENTOR.GRID_D_RANGE[0] >= cfg.AUGMENTOR.GRID_D_RANGE[1]:
                raise ValueError("cfg.AUGMENTOR.GRID_D_RANGE[0] needs to be larger than cfg.AUGMENTOR.GRID_D_RANGE[1]"
                                "Provided {}".format(cfg.AUGMENTOR.GRID_D_RANGE))
            if not check_value(cfg.AUGMENTOR.GRID_D_RANGE):
                raise ValueError("cfg.AUGMENTOR.GRID_D_RANGE values not in [0, 1] range")
            if not check_value(cfg.AUGMENTOR.GRID_ROTATE):
                raise ValueError("AUGMENTOR.GRID_ROTATE not in [0, 1] range")
                             
    #### Post-processing ####
    if cfg.TEST.POST_PROCESSING.REMOVE_CLOSE_POINTS:
        if len(cfg.DATA.TEST.RESOLUTION) == 1:
            raise ValueError("'DATA.TEST.RESOLUTION' must be set when using 'TEST.POST_PROCESSING.REMOVE_CLOSE_POINTS'")
        if len(cfg.DATA.TEST.RESOLUTION) != dim_count:
            raise ValueError("'DATA.TEST.RESOLUTION' must match in length to {}, which is the number of "
                             "dimensions".format(dim_count))