clearvars -global hSI2 hSICtl2;
global hSI2
global hSICtl2
configDir = fileparts(mfilename('fullpath'));
mdfPath = fullfile(configDir, '250707_MDF_P2.m');
usrPath = fullfile(configDir, 'usr_path2.usr');
[hSI2,hSICtl2] = scanimage(mdfPath, usrPath);
while or(~exist('hSI2'),~exist('hSICtl2'))
    drawnow
end
seedConfigDialogPathsAndLoadDefaultCfg(hSI2, configDir);
hSI2.hScan2D.logFramesPerFile=5000;

function seedConfigDialogPathsAndLoadDefaultCfg(hSiObj, configDir)
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
    hCfg.cfgLoadConfig(fullfile(configDir, cfgCandidates(1).name));
end
end
