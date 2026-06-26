function ensureBundledScanImageOnPath()
%ENSUREBUNDLEDSCANIMAGEONPATH Add the bundled ScanImage tree to the MATLAB path.
%   This workspace includes a bundled ScanImage source tree. Helper functions in
%   this repo can call into ScanImage classes directly when MATLAB is started from
%   a plain shell by first ensuring that tree is on the path.

persistent initialized
if ~isempty(initialized) && initialized
    return;
end

if exist('scanimage.mroi.RoiGroup', 'class') == 8
    initialized = true;
    return;
end

workspaceDir = fileparts(mfilename('fullpath'));
matches = dir(fullfile(workspaceDir, 'SI-Premium_*'));
matches = matches([matches.isdir]);
assert(~isempty(matches), 'Could not locate bundled ScanImage source tree under %s.', workspaceDir);

scanImageRoot = fullfile(matches(1).folder, matches(1).name);
addpath(genpath(scanImageRoot));

assert(exist('scanimage.mroi.RoiGroup', 'class') == 8, ...
    'Bundled ScanImage tree was added, but scanimage.mroi.RoiGroup is still unavailable.');
initialized = true;
end
