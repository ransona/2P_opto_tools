global hSI2
global hSICtl2
global siMotorData

FileObj = java.io.File('F:\');
free_gb = FileObj.getFreeSpace * 1e-9;
if free_gb < 200
    warning('start_script:LowDiskSpace', 'Free disk space is low: %.0f GB', free_gb);
end

disp('=======');
disp(['Received start for ', expID]);

if ~strcmp(hSI2.acqState,'idle')
    disp('Already acquiring so aborting');
    hSI2.abort;
    startAbort = tic;
    while ~strcmp(hSI2.acqState,'idle') && toc(startAbort) < 10
        drawnow();
    end
    if ~strcmp(hSI2.acqState,'idle')
        error('Timed out waiting for ScanImage to become idle');
    end
end

if ~exist(expDir,'dir')
    mkdir(expDir);
end

if ~isempty(expDirRemote) && ~exist(expDirRemote,'dir')
    mkdir(expDirRemote);
end

hSI2.hScan2D.logFilePath = expDir;
hSI2.hScan2D.logFileStem = [expID,'_2P'];

selectedScanfieldMetaPath = '';
try
    [~, selectedScanfieldMetaPath] = saveSelectedScanfieldToFolder(hSI2, hSICtl2, expDir, expID);
    if ~isempty(selectedScanfieldMetaPath)
        disp(['Saved selected scanfield metadata to ', selectedScanfieldMetaPath]);
    end
catch ME
    warning('start_script:SelectedScanfieldSaveFailed', ...
        'Failed to save selected scanfield metadata: %s', ME.message);
end

imagingMeta = struct();
if exist('siMotorData','var') && ~isempty(siMotorData) && isfield(siMotorData,'currentRoi')
    imagingMeta.currentRoi = siMotorData.currentRoi;
end
if ~isempty(selectedScanfieldMetaPath)
    [~, scanfieldMetaName, scanfieldMetaExt] = fileparts(selectedScanfieldMetaPath);
    imagingMeta.selectedScanfieldFile = [scanfieldMetaName, scanfieldMetaExt];
end
if ~isempty(fieldnames(imagingMeta))
    metaPath = fullfile(expDir,[expID,'_imageMeta.mat']);
    save(metaPath,'imagingMeta');
end

hSI2.hChannels.loggingEnable = true;
hSI2.startGrab;
disp('Requested start grabbing');
startGrab = tic;
while ~strcmp(hSI2.acqState,'grab') && toc(startGrab) < 10
    drawnow();
end

if ~strcmp(hSI2.acqState,'grab')
    error('Timed out waiting for ScanImage to enter grab state');
end

disp('Grabbing confirmed');
