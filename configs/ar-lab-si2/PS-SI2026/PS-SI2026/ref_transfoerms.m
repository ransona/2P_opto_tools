load(fullfile(configDir, 'GGtoP1_working.mat'))
hSI.hScan_RGG_P2.scannerToRefTransform=tf;
load(fullfile(configDir, 'SLMtoGG_working.mat'))
hSI.hSlmScan.scannerToRefTransform=tf;