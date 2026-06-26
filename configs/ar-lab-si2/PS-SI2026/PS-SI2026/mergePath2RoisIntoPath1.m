function [savePath, mergedRoiGroup] = mergePath2RoisIntoPath1(experimentDir, outputPath)
%MERGEPATH2ROISINTOPATH1 Merge P2 ROIs into the P1 frame and save a .roi file.
%   MERGEPATH2ROISINTOPATH1() opens a folder picker for the experiment
%   directory, looks for ROI files in the P1 and P2 subfolders, transforms
%   the P2 ROIs into the P1 frame, appends them to the P1 ROI group, and
%   saves a ScanImage-native .roi file.
%
%   [SAVEPATH, MERGEDROIGROUP] = MERGEPATH2ROISINTOPATH1(EXPERIMENTDIR)
%   uses the specified experiment directory instead of prompting.
%
%   [...] = MERGEPATH2ROISINTOPATH1(EXPERIMENTDIR, OUTPUTPATH) saves to the
%   provided output path instead of the default merged filename.

if nargin < 1 || isempty(experimentDir)
    experimentDir = uigetdir(pwd, 'Select experiment folder containing P1 and P2 ROI files');
    if isequal(experimentDir, 0)
        savePath = '';
        mergedRoiGroup = [];
        return;
    end
end

experimentDir = char(experimentDir);
assert(exist(experimentDir, 'dir') == 7, 'Experiment directory does not exist: %s', experimentDir);

path1RoiPath = findLatestExperimentRoiFile(experimentDir, 'P1');
path2RoiPath = findLatestExperimentRoiFile(experimentDir, 'P2');

fprintf('Using P1 ROI file:\n%s\n', path1RoiPath);
fprintf('Using P2 ROI file:\n%s\n', path2RoiPath);

path1RoiGroup = scanimage.mroi.RoiGroup.loadFromFile(path1RoiPath);
path2RoiGroup = scanimage.mroi.RoiGroup.loadFromFile(path2RoiPath);

mergedRoiGroup = path1RoiGroup;
path1Rois = path1RoiGroup.rois;

for roiIndex = 1:numel(path2RoiGroup.rois)
    matchingPath1Roi = [];
    if roiIndex <= numel(path1Rois)
        matchingPath1Roi = path1Rois(roiIndex);
    end

    transformedRoi = transformPath2RoiIntoPath1(path2RoiGroup.rois(roiIndex), matchingPath1Roi, roiIndex);
    mergedRoiGroup.add(transformedRoi);
end

mergedRoiGroup.name = sprintf('%s + P2', path1RoiGroup.name);
mergedRoiGroup.UserData = updateMergedUserData(mergedRoiGroup.UserData, numel(mergedRoiGroup.rois));

if nargin < 2 || isempty(outputPath)
    outputPath = buildDefaultOutputPath(experimentDir, path1RoiPath);
end

savePath = char(outputPath);
mergedRoiGroup.saveToFile(savePath);

fprintf('Merged ROI file saved to:\n%s\n', savePath);

end


function roiPath = findLatestExperimentRoiFile(experimentDir, systemName)
preferredDir = fullfile(experimentDir, systemName);
roiPath = '';

if exist(preferredDir, 'dir') == 7
    roiPath = pickLatestRoiFile(preferredDir);
end

if isempty(roiPath)
    roiFiles = dir(fullfile(experimentDir, '**', '*.roi'));
    roiFiles = roiFiles(~[roiFiles.isdir]);

    if ~isempty(roiFiles)
        folderMask = contains(lower({roiFiles.folder}), [filesep lower(systemName)]);
        roiFiles = roiFiles(folderMask);
        roiPath = pickLatestRoiFromListing(roiFiles);
    end
end

if isempty(roiPath)
    [fileName, fileDir] = uigetfile({'*.roi','ROI files (*.roi)'}, sprintf('Select %s ROI file', systemName), experimentDir);
    if isequal(fileName, 0)
        error('No ROI file selected for %s.', systemName);
    end
    roiPath = fullfile(fileDir, fileName);
end
end


function roiPath = pickLatestRoiFile(folderPath)
roiFiles = dir(fullfile(folderPath, '*_selectedScanfield.roi'));
roiFiles = roiFiles(~[roiFiles.isdir]);

if isempty(roiFiles)
    roiFiles = dir(fullfile(folderPath, '*.roi'));
    roiFiles = roiFiles(~[roiFiles.isdir]);
end

roiPath = pickLatestRoiFromListing(roiFiles);
end


function roiPath = pickLatestRoiFromListing(roiFiles)
roiPath = '';

if isempty(roiFiles)
    return;
end

[~, order] = sort([roiFiles.datenum], 'descend');
roiFiles = roiFiles(order);
roiPath = fullfile(roiFiles(1).folder, roiFiles(1).name);
end


function outputPath = buildDefaultOutputPath(experimentDir, path1RoiPath)
[~, fileName] = fileparts(path1RoiPath);
expId = erase(fileName, '_selectedScanfield');
outputPath = fullfile(experimentDir, [expId '_merged_P1_P2.roi']);
end


function transformedRoi = transformPath2RoiIntoPath1(path2Roi, matchingPath1Roi, roiIndex)
transformedRoi = scanimage.mroi.Roi();
transformedRoi.name = sprintf('P2 %s', getFallbackName(path2Roi.name, roiIndex));
transformedRoi.UserData = path2Roi.UserData;
transformedRoi.enable = path2Roi.enable;
transformedRoi.display = path2Roi.display;
transformedRoi.discretePlaneMode = path2Roi.discretePlaneMode;
transformedRoi.powerFractions = path2Roi.powerFractions;
transformedRoi.powers = path2Roi.powers;
transformedRoi.pzAdjust = path2Roi.pzAdjust;
transformedRoi.Lzs = path2Roi.Lzs;
transformedRoi.interlaceDecimation = path2Roi.interlaceDecimation;
transformedRoi.interlaceOffset = path2Roi.interlaceOffset;

for scanfieldIndex = 1:numel(path2Roi.scanfields)
    matchingPath1Scanfield = [];
    if ~isempty(matchingPath1Roi) && scanfieldIndex <= numel(matchingPath1Roi.scanfields)
        matchingPath1Scanfield = matchingPath1Roi.scanfields(scanfieldIndex);
    end

    transformedScanfield = transformPath2ScanfieldIntoPath1(path2Roi.scanfields(scanfieldIndex), matchingPath1Scanfield);
    transformedScanfield.UserData = path2Roi.scanfields(scanfieldIndex).UserData;
    transformedRoi.add(path2Roi.zs(scanfieldIndex), transformedScanfield);
end
end


function transformedScanfield = transformPath2ScanfieldIntoPath1(path2Scanfield, matchingPath1Scanfield)
assert(isa(path2Scanfield, 'scanimage.mroi.scanfield.fields.RotatedRectangle'), ...
    'This helper currently expects RotatedRectangle imaging scanfields.');

system2ScannerToRefTransform = getSystem2ScannerToRefTransform();

centerXY = double(path2Scanfield.centerXY);
sizeXY = double(path2Scanfield.sizeXY);
rotationDegrees = double(path2Scanfield.rotationDegrees);

widthVector = rotateVector([sizeXY(1) 0], rotationDegrees);
heightVector = rotateVector([0 sizeXY(2)], rotationDegrees);

transformedCenterXY = applyAffine2D(centerXY, system2ScannerToRefTransform);
transformedWidthEnd = applyAffine2D(centerXY + widthVector, system2ScannerToRefTransform);
transformedHeightEnd = applyAffine2D(centerXY + heightVector, system2ScannerToRefTransform);

transformedWidthVector = transformedWidthEnd - transformedCenterXY;
transformedHeightVector = transformedHeightEnd - transformedCenterXY;

widthNorm = norm(transformedWidthVector);
heightNorm = norm(transformedHeightVector);
assert(widthNorm > 0 && heightNorm > 0, 'Transformed scanfield collapsed to zero size.');

orthogonality = dot(transformedWidthVector, transformedHeightVector);
assert(abs(orthogonality) <= 1e-6 * widthNorm * heightNorm, ...
    ['The transformed P2 scanfield is not representable as a RotatedRectangle. ' ...
     'This helper expects a translation/scale/rotation transform without shear.']);

transformedSizeXY = [widthNorm heightNorm];
transformedRotationDegrees = mod(atan2d(transformedWidthVector(2), transformedWidthVector(1)), 360);
transformedPixelResolutionXY = max(round(double(path2Scanfield.pixelResolutionXY)), 1);

if ~isempty(matchingPath1Scanfield)
    transformedPixelResolutionXY = matchScanfieldResolutionToPath1(transformedSizeXY, matchingPath1Scanfield);
end

transformedScanfield = path2Scanfield.copy();
transformedScanfield.UserData = path2Scanfield.UserData;
transformedScanfield.centerXY = transformedCenterXY;
transformedScanfield.sizeXY = transformedSizeXY;
transformedScanfield.rotationDegrees = transformedRotationDegrees;
transformedScanfield.pixelResolutionXY = transformedPixelResolutionXY;
end


function transformedPixelResolutionXY = matchScanfieldResolutionToPath1(transformedSizeXY, matchingPath1Scanfield)
path1SizeXY = double(matchingPath1Scanfield.sizeXY);
path1PixelResolutionXY = double(matchingPath1Scanfield.pixelResolutionXY);

pixelsPerRefX = path1PixelResolutionXY(1) / path1SizeXY(1);
pixelsPerRefY = path1PixelResolutionXY(2) / path1SizeXY(2);

transformedPixelResolutionXY = [ ...
    max(1, round(transformedSizeXY(1) * pixelsPerRefX)), ...
    max(1, round(transformedSizeXY(2) * pixelsPerRefY))];
end


function userData = updateMergedUserData(userData, roiCount)
if isempty(userData)
    userData = struct();
elseif ~isstruct(userData)
    userData = struct('originalUserData', userData);
end

if isfield(userData, 'selectedScanfieldMetadata') && isstruct(userData.selectedScanfieldMetadata)
    userData.selectedScanfieldMetadata.roiCount = roiCount;
    userData.selectedScanfieldMetadata.mergedPath2IntoPath1 = true;
    userData.selectedScanfieldMetadata.path2ScannerToRefTransform = getSystem2ScannerToRefTransform();
else
    userData.mergedPath2IntoPath1 = true;
    userData.path2ScannerToRefTransform = getSystem2ScannerToRefTransform();
end
end


function transformedPointXY = applyAffine2D(pointXY, transformMatrix)
pointXY = double(pointXY(:)');
assert(numel(pointXY) == 2, 'Expected a 2-element [x y] point.');
assert(isequal(size(transformMatrix), [3 3]), 'Expected a 3x3 affine transform.');

x = pointXY(1);
y = pointXY(2);

xOut = transformMatrix(1,1) * x + transformMatrix(1,2) * y + transformMatrix(1,3);
yOut = transformMatrix(2,1) * x + transformMatrix(2,2) * y + transformMatrix(2,3);
wOut = transformMatrix(3,1) * x + transformMatrix(3,2) * y + transformMatrix(3,3);

assert(abs(wOut) > eps, 'Affine transform produced homogeneous w = 0.');
transformedPointXY = [xOut / wOut, yOut / wOut];
end


function rotatedVectorXY = rotateVector(vectorXY, rotationDegrees)
rotationRadians = deg2rad(rotationDegrees);
cosTheta = cos(rotationRadians);
sinTheta = sin(rotationRadians);

x = vectorXY(1);
y = vectorXY(2);

rotatedVectorXY = [ ...
    cosTheta * x - sinTheta * y, ...
    sinTheta * x + cosTheta * y];
end


function name = getFallbackName(name, roiIndex)
if isempty(name)
    name = sprintf('ROI %d', roiIndex);
end
end


function transformMatrix = getSystem2ScannerToRefTransform()
transformMatrix = [ ...
    0.9983541241365312, 0, 7.206723324972624; ...
    0, 0.9931332611638753, 6.6419426251376885; ...
    0, 0, 1];
end
