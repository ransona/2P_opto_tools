clearvars -global hSI hSICtl;
global hSI
global hSICtl
[hSI,hSICtl] = scanimage('C:\Users\ScanImage\Documents\MATLAB\250707_MDF_P1\250707_MDF_P1.m','C:\Users\ScanImage\Documents\MATLAB\usr_path1.usr');
hSI.hScan2D.logFramesPerFile=5000;
% while or(~exist('hSI'),~exist('hSICtl'))
    while or(~exist('hSI'),~exist('hSICtl'))
    % wait for SI to make these stuctures
    drawnow
    end
