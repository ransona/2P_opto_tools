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
seedConfigDialogPaths(hSI, configDir);

function seedConfigDialogPaths(hSiObj, configDir)
if isempty(hSiObj) || ~isprop(hSiObj, 'hConfigurationSaver')
    return;
end

hCfg = hSiObj.hConfigurationSaver;
hCfg.setClassDataVar('lastConfigFilePath', configDir, hCfg.classDataFileName);
hCfg.setClassDataVar('lastFastConfigFilePath', configDir, hCfg.classDataFileName);

usrCandidates = dir(fullfile(configDir, 'usr_path*.usr'));
if ~isempty(usrCandidates)
    hCfg.setClassDataVar('lastUsrFile', fullfile(configDir, usrCandidates(1).name), hCfg.classDataFileName);
end

cfgCandidates = dir(fullfile(configDir, 'cfg_path*.cfg'));
if ~isempty(cfgCandidates)
    hCfg.cfgFilename = fullfile(configDir, cfgCandidates(1).name);
end
end
