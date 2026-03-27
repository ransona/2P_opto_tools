clearvars -global hSI hSICtl;
global hSI
global hSICtl
configDir = fileparts(mfilename('fullpath'));
mdfPath = fullfile(configDir, '250707_MDF_P1.m');
usrPath = fullfile(configDir, 'usr_path1.usr');
[hSI,hSICtl] = scanimage(mdfPath, usrPath);
hSI.hScan2D.logFramesPerFile=5000;
while or(~exist('hSI'),~exist('hSICtl'))
    drawnow
end
