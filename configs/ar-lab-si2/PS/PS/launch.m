clearvars -global hSI hSICtl;
global hSI
global hSICtl
configDir = fileparts(mfilename('fullpath'));
mdfPath = fullfile(configDir, '250710_MDF_RGG-RGG - all except SLM.m');
scanimageRoot = 'C:\Program Files\Vidrio\SI-Premium_2023.1.1_(2025-06-27)_d280f351';
addpath(genpath(scanimageRoot), '-begin');
[hSI,hSICtl] = scanimage(mdfPath);
while or(~exist('hSI'),~exist('hSICtl'))
    drawnow
end
seedConfigDialogPathsAndLoadDefaultCfg(hSI, configDir);
hSI.hScan2D.logFramesPerFile=5000;

load(fullfile(configDir, 'GGtoP1_working.mat'))
hSI.hScan_RGG_P2.scannerToRefTransform=tf;
load(fullfile(configDir, 'SLMtoGG_working.mat'))
hSI.hSlmScan.scannerToRefTransform=tf;

rs = dabs.resources.ResourceStore();
wg = rs.filterByName('Turnaround Blanking P2 via P1 Sync');
which dabs.generic.waveforms.digital.TurnaroundBlankClock -all
wg.stopTask();
wg.sampleRate_Hz = 20e6;
wg.dutyCycle = 100;
scan = wg.wvfmParams.linePeriodScan;
wg.startDelay = 19.82e-05;
wg.refreshWvfmParams();
disp(wg.wvfmParams)
w = wg.computeWaveform();
fprintf('N=%d, high_frac=%.3f\n', numel(w), mean(w>0));
wg.startTask();

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