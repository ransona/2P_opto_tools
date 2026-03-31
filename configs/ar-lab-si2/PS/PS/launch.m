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
hSI.hScan2D.logFramesPerFile=5000;
seedConfigDialogPaths(hSI, configDir);

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
