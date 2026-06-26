function [centersXY, names] = extractStimulusCenters(roiGroupOrPath)
%EXTRACTSTIMULUSCENTERS Return stimulus target coordinates and names.
%   [CENTERSXY, NAMES] = EXTRACTSTIMULUSCENTERS(ROIGROUPORPATH) accepts either a
%   ScanImage RoiGroup object or a .roi file path and returns:
%       CENTERSXY : Nx2 double array of stimulus target positions
%       NAMES     : Nx1 cell array of target names

targets = enumerateStimulusTargets(roiGroupOrPath);
centersXY = zeros(numel(targets), 2);
names = cell(numel(targets), 1);
for targetIndex = 1:numel(targets)
    centersXY(targetIndex, :) = targets(targetIndex).centerXY;
    names{targetIndex} = targets(targetIndex).name;
end
end
