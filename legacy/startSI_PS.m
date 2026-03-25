clearvars –global hSI hSICtl;
global hSI
hSI.hScan2D.logFramesPerFile=5000;
global hSICtl
[hSI,hSICtl] = scanimage('C:\Users\ScanImage\Documents\MATLAB\250710_MDF_RGG-RGG - all except SLM\250710_MDF_RGG-RGG - all except SLM.m');
% while or(~exist('hSI'),~exist('hSICtl'))
    while or(~exist('hSI'),~exist('hSICtl'))
    % wait for SI to make these stuctures
    drawnow
    end
%siListener

load('GGtoP1_working.mat')
hSI.hScan_RGG_P2.scannerToRefTransform=tf;
load('SLMtoGG_working.mat')
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
