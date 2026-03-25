clearvars -global hSI2 hSICtl2;
global hSI2
global hSICtl2
[hSI2,hSICtl2] = scanimage('C:\Users\ScanImage\Documents\MATLAB\250707_MDF_P2\250707_MDF_P2.m','C:\Users\ScanImage\Documents\MATLAB\usr_path2.usr');
hSI2.hScan2D.logFramesPerFile=5000;
% while or(~exist('hSI'),~exist('hSICtl'))
    while or(~exist('hSI2'),~exist('hSICtl2'))
    % wait for SI to make these stuctures
    drawnow
    end
