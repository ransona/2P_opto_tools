function [selectedScanfieldMeta, savePath] = saveSelectedScanfieldToFolder(hSI, hSICtl, saveDir, expID)
selectedScanfieldMeta = [];
savePath = '';

if ~exist(saveDir, 'dir')
    mkdir(saveDir);
end

currentRoiGroup = hSI.hRoiManager.currentRoiGroup;
if ~most.idioms.isValidObj(currentRoiGroup) || isempty(currentRoiGroup.rois)
    return;
end

[selectedScanfield, parentRoi, selectedScanfieldZ, selectionSource, selectionDetails] = resolveSelectedScanfield(hSI, hSICtl);

selectedScanfieldMeta = buildSelectedScanfieldMetadata( ...
    hSI, currentRoiGroup, selectedScanfield, parentRoi, selectedScanfieldZ, selectionSource, selectionDetails, expID);

savePath = fullfile(saveDir, [expID, '_selectedScanfield.roi']);
saveCurrentRoiGroupWithMetadata(currentRoiGroup, selectedScanfieldMeta, savePath);
end


function selectedScanfieldMeta = buildSelectedScanfieldMetadata(hSI, currentRoiGroup, selectedScanfield, parentRoi, selectedScanfieldZ, selectionSource, selectionDetails, expID)
global siMotorData;

selectedScanfieldMeta = struct();
selectedScanfieldMeta.expID = expID;
selectedScanfieldMeta.savedAt = char(datetime('now', 'Format', 'yyyy-MM-dd HH:mm:ss'));
selectedScanfieldMeta.saveMethod = 'roiGroup.saveToFile';
selectedScanfieldMeta.mroiEnable = logical(hSI.hRoiManager.mroiEnable);
selectedScanfieldMeta.roiGroupClass = class(currentRoiGroup);
selectedScanfieldMeta.roiGroupName = currentRoiGroup.name;
selectedScanfieldMeta.roiGroupUuid = currentRoiGroup.uuid;
selectedScanfieldMeta.roiCount = numel(currentRoiGroup.rois);
selectedScanfieldMeta.selectionSource = selectionSource;
selectedScanfieldMeta.selectionDetails = selectionDetails;
selectedScanfieldMeta.currentRoi = [];
selectedScanfieldMeta.rois = arrayfun(@(roi)buildRoiMetadata(hSI, roi), currentRoiGroup.rois, 'UniformOutput', false);

selectedScanfieldMeta.scanfieldClass = '';
selectedScanfieldMeta.scanfieldName = '';
selectedScanfieldMeta.scanfieldUuid = '';
selectedScanfieldMeta.scanfieldZ = selectedScanfieldZ;
selectedScanfieldMeta.centerXY = [];
selectedScanfieldMeta.sizeXY = [];
selectedScanfieldMeta.rotationDegrees = [];
selectedScanfieldMeta.pixelResolutionXY = [];
selectedScanfieldMeta.objectiveResolution = [];
selectedScanfieldMeta.fieldSizeUMXY = [];
selectedScanfieldMeta.umPerPixelXY = [];
selectedScanfieldMeta.roiClass = '';
selectedScanfieldMeta.roiName = '';
selectedScanfieldMeta.roiUuid = '';
selectedScanfieldMeta.scanfieldIndex = [];

if ~isempty(selectedScanfield)
    selectedScanfieldMeta.scanfieldClass = class(selectedScanfield);
    selectedScanfieldMeta.scanfieldName = selectedScanfield.name;
    selectedScanfieldMeta.scanfieldUuid = selectedScanfield.uuid;
    selectedScanfieldMeta.centerXY = getPropIfPresent(selectedScanfield, 'centerXY');
    selectedScanfieldMeta.sizeXY = getPropIfPresent(selectedScanfield, 'sizeXY');
    selectedScanfieldMeta.rotationDegrees = getPropIfPresent(selectedScanfield, 'rotationDegrees');
    selectedScanfieldMeta.pixelResolutionXY = getPropIfPresent(selectedScanfield, 'pixelResolutionXY');
    selectedScanfieldMeta.objectiveResolution = getObjectiveResolution(hSI);

    if ~isempty(selectedScanfieldMeta.objectiveResolution) && ~isempty(selectedScanfieldMeta.sizeXY)
        selectedScanfieldMeta.fieldSizeUMXY = selectedScanfieldMeta.sizeXY .* selectedScanfieldMeta.objectiveResolution;
    end

    if ~isempty(selectedScanfieldMeta.fieldSizeUMXY) && ~isempty(selectedScanfieldMeta.pixelResolutionXY)
        selectedScanfieldMeta.umPerPixelXY = selectedScanfieldMeta.fieldSizeUMXY ./ selectedScanfieldMeta.pixelResolutionXY;
    end

    if ~isempty(parentRoi)
        selectedScanfieldMeta.roiClass = class(parentRoi);
        selectedScanfieldMeta.roiName = parentRoi.name;
        selectedScanfieldMeta.roiUuid = parentRoi.uuid;
        selectedScanfieldMeta.scanfieldIndex = find(parentRoi.scanfields == selectedScanfield, 1, 'first');
    end
end

if ~isempty(siMotorData) && isstruct(siMotorData) && isfield(siMotorData, 'currentRoi')
    selectedScanfieldMeta.currentRoi = siMotorData.currentRoi;
end
end


function roiMeta = buildRoiMetadata(hSI, roi)
roiMeta = struct();
roiMeta.roiClass = class(roi);
roiMeta.roiName = roi.name;
roiMeta.roiUuid = roi.uuid;
roiMeta.enable = roi.enable;
roiMeta.display = roi.display;
roiMeta.discretePlaneMode = roi.discretePlaneMode;
roiMeta.zs = roi.zs;
roiMeta.scanfieldCount = numel(roi.scanfields);
roiMeta.scanfields = arrayfun(@(idx)buildScanfieldMetadata(hSI, roi, idx), 1:numel(roi.scanfields), 'UniformOutput', false);
end


function scanfieldMeta = buildScanfieldMetadata(hSI, roi, idx)
scanfield = roi.scanfields(idx);

scanfieldMeta = struct();
scanfieldMeta.scanfieldClass = class(scanfield);
scanfieldMeta.scanfieldName = scanfield.name;
scanfieldMeta.scanfieldUuid = scanfield.uuid;
scanfieldMeta.scanfieldIndex = idx;
scanfieldMeta.scanfieldZ = [];
scanfieldMeta.enable = scanfield.enable;
scanfieldMeta.centerXY = getPropIfPresent(scanfield, 'centerXY');
scanfieldMeta.sizeXY = getPropIfPresent(scanfield, 'sizeXY');
scanfieldMeta.rotationDegrees = getPropIfPresent(scanfield, 'rotationDegrees');
scanfieldMeta.pixelResolutionXY = getPropIfPresent(scanfield, 'pixelResolutionXY');
scanfieldMeta.objectiveResolution = getObjectiveResolution(hSI);
scanfieldMeta.fieldSizeUMXY = [];
scanfieldMeta.umPerPixelXY = [];

if numel(roi.zs) >= idx
    scanfieldMeta.scanfieldZ = roi.zs(idx);
end

if ~isempty(scanfieldMeta.objectiveResolution) && ~isempty(scanfieldMeta.sizeXY)
    scanfieldMeta.fieldSizeUMXY = scanfieldMeta.sizeXY .* scanfieldMeta.objectiveResolution;
end

if ~isempty(scanfieldMeta.fieldSizeUMXY) && ~isempty(scanfieldMeta.pixelResolutionXY)
    scanfieldMeta.umPerPixelXY = scanfieldMeta.fieldSizeUMXY ./ scanfieldMeta.pixelResolutionXY;
end
end


function saveCurrentRoiGroupWithMetadata(currentRoiGroup, selectedScanfieldMeta, savePath)
originalUserData = currentRoiGroup.UserData;
currentRoiGroup.UserData = mergeUserData(originalUserData, selectedScanfieldMeta);

try
    currentRoiGroup.saveToFile(savePath);
catch ME
    currentRoiGroup.UserData = originalUserData;
    rethrow(ME);
end

currentRoiGroup.UserData = originalUserData;
end


function mergedUserData = mergeUserData(originalUserData, selectedScanfieldMeta)
if isempty(originalUserData)
    mergedUserData = struct();
elseif isstruct(originalUserData)
    mergedUserData = originalUserData;
else
    mergedUserData = struct('originalUserData', originalUserData);
end

mergedUserData.selectedScanfieldMetadata = selectedScanfieldMeta;
end


function [selectedScanfield, parentRoi, selectedScanfieldZ, selectionSource, selectionDetails] = resolveSelectedScanfield(~, hSICtl)
selectedScanfield = [];
parentRoi = [];
selectedScanfieldZ = [];
selectionSource = '';
selectionDetails = struct( ...
    'editorSelectionClass', '', ...
    'editorZ', [], ...
    'usedFallback', false);

if ~most.idioms.isValidObj(hSICtl) || ~isprop(hSICtl, 'hRoiGroupEditor') || ~most.idioms.isValidObj(hSICtl.hRoiGroupEditor)
    return;
end

hEditor = hSICtl.hRoiGroupEditor;
selectionDetails.editorZ = hEditor.editorZ;

if ~most.idioms.isValidObj(hEditor.selectedObj)
    return;
end

selectionDetails.editorSelectionClass = class(hEditor.selectedObj);

if isa(hEditor.selectedObj, 'scanimage.mroi.scanfield.ScanField')
    selectedScanfield = pickScalarScanfield(hEditor.selectedObj);
    if most.idioms.isValidObj(hEditor.selectedObjParent) && isa(hEditor.selectedObjParent, 'scanimage.mroi.Roi')
        parentRoi = hEditor.selectedObjParent;
    end
    selectionSource = 'roiGroupEditor.selectedScanfield';
elseif isa(hEditor.selectedObj, 'scanimage.mroi.Roi')
    parentRoi = hEditor.selectedObj;
    selectedScanfield = resolveRoiSelection(parentRoi, hEditor.editorZ);
    if ~isempty(selectedScanfield)
        selectionSource = 'roiGroupEditor.selectedRoi';
    end
end

selectedScanfieldZ = inferSelectedScanfieldZ(parentRoi, selectedScanfield, hEditor.editorZ);
end


function scanfield = resolveRoiSelection(parentRoi, editorZ)
scanfield = [];

if nargin >= 2 && isnumeric(editorZ) && isscalar(editorZ) && ~isnan(editorZ)
    scanfield = pickScalarScanfield(parentRoi.get(editorZ, true));
end

if isempty(scanfield)
    scanfield = pickScalarScanfield(parentRoi.scanfields);
end
end


function selectedScanfieldZ = inferSelectedScanfieldZ(parentRoi, selectedScanfield, fallbackZ)
selectedScanfieldZ = [];

if ~isempty(parentRoi) && ~isempty(selectedScanfield)
    scanfieldIdx = find(parentRoi.scanfields == selectedScanfield, 1, 'first');
    if ~isempty(scanfieldIdx) && numel(parentRoi.zs) >= scanfieldIdx
        selectedScanfieldZ = parentRoi.zs(scanfieldIdx);
        return;
    end
end

if nargin >= 3 && isnumeric(fallbackZ) && isscalar(fallbackZ) && ~isnan(fallbackZ)
    selectedScanfieldZ = fallbackZ;
end
end


function scanfield = pickScalarScanfield(candidate)
scanfield = [];

if isempty(candidate) || ~isa(candidate, 'scanimage.mroi.scanfield.ScanField')
    return;
end

scanfield = candidate(1);
end


function value = getPropIfPresent(obj, propName)
value = [];

if isprop(obj, propName)
    value = obj.(propName);
end
end


function objectiveResolution = getObjectiveResolution(hSI)
objectiveResolution = [];

if isprop(hSI, 'objectiveResolution') && ~isempty(hSI.objectiveResolution)
    objectiveResolution = hSI.objectiveResolution;
end
end
