clearvars -global hSI2 hSICtl2;
global hSI2
global hSICtl2
configDir = fileparts(mfilename('fullpath'));
mdfPath = fullfile(configDir, '250707_MDF_P2.m');
usrPath = fullfile(configDir, 'usr_path2.usr');
[hSI2,hSICtl2] = scanimage(mdfPath, usrPath);
hSI2.hScan2D.logFramesPerFile=5000;
while or(~exist('hSI2'),~exist('hSICtl2'))
    drawnow
end
