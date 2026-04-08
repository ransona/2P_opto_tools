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
%SEEDCONFIGDIALOGPATHSANDLOADDEFAULTCFG Seed ScanImage config paths and load a default CFG.
%   seedConfigDialogPathsAndLoadDefaultCfg(hSiObj, configDir) updates the
%   ConfigurationSaver class-data entries used by the config dialogs and
%   then loads the newest cfg_path*.cfg found in configDir.

if isempty(hSiObj) || ~isprop(hSiObj, 'hConfigurationSaver') || isempty(hSiObj.hConfigurationSaver)
    return;
end

hCfg = hSiObj.hConfigurationSaver;
configDir = char(configDir);
assert(exist(configDir, 'dir') == 7, 'Configuration directory does not exist: %s', configDir);

if isempty(hCfg.classDataFileName) || exist(hCfg.classDataFileName, 'file') ~= 2
    hCfg.reinit();
end

className = class(hCfg);
classDataFileName = hCfg.classDataFileName;

most.HasClassDataFile.setClassDataVarStatic( ...
    className, 'lastConfigFilePath', configDir, classDataFileName, false);
most.HasClassDataFile.setClassDataVarStatic( ...
    className, 'lastFastConfigFilePath', configDir, classDataFileName, false);

usrCandidates = dir(fullfile(configDir, 'usr_path*.usr'));
usrCandidates = usrCandidates(~[usrCandidates.isdir]);
if ~isempty(usrCandidates)
    usrPath = fullfile(usrCandidates(1).folder, usrCandidates(1).name);
    most.HasClassDataFile.setClassDataVarStatic( ...
        className, 'lastUsrFile', usrPath, classDataFileName, false);
end

cfgCandidates = dir(fullfile(configDir, 'cfg_path*.cfg'));
cfgCandidates = cfgCandidates(~[cfgCandidates.isdir]);
if isempty(cfgCandidates)
    return;
end

[~, newestIndex] = max([cfgCandidates.datenum]);
cfgPath = fullfile(cfgCandidates(newestIndex).folder, cfgCandidates(newestIndex).name);
hCfg.cfgLoadConfig(cfgPath);
end