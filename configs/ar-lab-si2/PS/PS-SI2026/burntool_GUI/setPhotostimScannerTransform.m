function setPhotostimScannerTransform(hSI, transformMatrix, varargin)
%SETPHOTOSTIMSCANNERTRANSFORM Apply a 3x3 scanner->reference transform to ScanImage.
%   SETPHOTOSTIMSCANNERTRANSFORM(HSI, T) writes T into the active photostim
%   scanner transform. This helper first tries the explicit dual-path handle
%   used in this workspace, then falls back to hSI.hPhotostim.hScan.
%
%   SETPHOTOSTIMSCANNERTRANSFORM(..., 'MatFilePath', PATH) also saves the
%   transform to PATH as variable T.

parser = inputParser();
parser.addRequired('hSI');
parser.addRequired('transformMatrix', @(x) isnumeric(x) && isequal(size(x), [3 3]));
parser.addParameter('MatFilePath', '', @(x) ischar(x) || isstring(x));
parser.parse(hSI, transformMatrix, varargin{:});

transformMatrix = double(transformMatrix);

if isprop(hSI, 'hScan_RGG_P2') && ~isempty(hSI.hScan_RGG_P2)
    hSI.hScan_RGG_P2.scannerToRefTransform = transformMatrix;
elseif isprop(hSI, 'hPhotostim') && ~isempty(hSI.hPhotostim) ...
        && isprop(hSI.hPhotostim, 'hScan') && ~isempty(hSI.hPhotostim.hScan)
    hSI.hPhotostim.hScan.scannerToRefTransform = transformMatrix;
else
    error(['Could not locate a writable photostim scanner transform on the provided hSI. ' ...
           'Expected hSI.hScan_RGG_P2 or hSI.hPhotostim.hScan.']);
end

matFilePath = char(parser.Results.MatFilePath);
if ~isempty(matFilePath)
    T = transformMatrix;
    save(matFilePath, 'T');
end
end
