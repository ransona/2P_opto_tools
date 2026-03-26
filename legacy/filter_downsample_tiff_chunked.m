function report = filter_downsample_tiff_chunked(inputTiff, varargin)
% filter_downsample_tiff_chunked
% Read a multi-frame TIFF in chunks, apply 3D Gaussian filtering, downsample
% in time, and save incrementally using append writes.
%
% Usage:
%   report = filter_downsample_tiff_chunked('F:\PhotoStim\group_001_long_interp.tif');
%
% Name-value options:
%   'OutputTiff'        : default <input>_zfilt_ds2.tif
%   'ChunkFrames'       : default 5000 (core frames processed per chunk)
%   'SigmaXY'           : default 3
%   'SigmaZ'            : default 7
%   'DownsampleFactor'  : default 2 (temporal)
%   'Compression'       : default 'none' ('none' | 'lzw' | 'deflate')
%   'OutputClass'       : 'same' (default) | 'uint8' | 'uint16' | 'int16' | 'single' | 'double'
%   'UseBigTiff'        : default true (recommended for large outputs)
%   'Overwrite'         : default true
%   'Verbose'           : default true
%
% Notes:
% - Temporal downsampling is global across the whole stack (frame 1-based).
% - A Z halo (~3*sigmaZ) is used to reduce chunk boundary artifacts.

    if nargin < 1 || isempty(inputTiff)
        [f, pth] = uigetfile({'*.tif;*.tiff', 'TIFF files (*.tif, *.tiff)'}, ...
            'Select input TIFF');
        if isequal(f, 0)
            error('No input TIFF selected.');
        end
        inputTiff = fullfile(pth, f);
    end
    if isstring(inputTiff), inputTiff = char(inputTiff); end
    assert(exist(inputTiff, 'file') == 2, 'Input TIFF not found: %s', inputTiff);

    p = inputParser;
    p.addParameter('OutputTiff', '', @(v)ischar(v) || isstring(v));
    p.addParameter('ChunkFrames', 5000, @(v)isnumeric(v) && isscalar(v) && isfinite(v) && v >= 1);
    p.addParameter('SigmaXY', 3, @(v)isnumeric(v) && isscalar(v) && isfinite(v) && v >= 0);
    p.addParameter('SigmaZ', 7, @(v)isnumeric(v) && isscalar(v) && isfinite(v) && v >= 0);
    p.addParameter('DownsampleFactor', 2, @(v)isnumeric(v) && isscalar(v) && isfinite(v) && v >= 1);
    p.addParameter('Compression', 'none', @(v)ischar(v) || isstring(v));
    p.addParameter('OutputClass', 'same', @(v)ischar(v) || isstring(v));
    p.addParameter('UseBigTiff', true, @(v)islogical(v) && isscalar(v));
    p.addParameter('Overwrite', true, @(v)islogical(v) && isscalar(v));
    p.addParameter('Verbose', true, @(v)islogical(v) && isscalar(v));
    p.parse(varargin{:});

    outPath = char(p.Results.OutputTiff);
    if isempty(outPath)
        [inDir, inBase, ~] = fileparts(inputTiff);
        outPath = fullfile(inDir, [inBase '_zfilt_ds2.tif']);
    end
    chunkFrames = round(double(p.Results.ChunkFrames));
    sigmaXY = double(p.Results.SigmaXY);
    sigmaZ = double(p.Results.SigmaZ);
    ds = max(1, round(double(p.Results.DownsampleFactor)));
    compression = char(p.Results.Compression);
    outClassReq = lower(char(p.Results.OutputClass));
    useBigTiff = p.Results.UseBigTiff;
    overwrite = p.Results.Overwrite;
    verbose = p.Results.Verbose;

    useInfo = true;
    info = [];
    try
        info = imfinfo(inputTiff);
        nIn = numel(info);
        assert(nIn >= 1, 'Input TIFF has no frames.');
        firstFrame = imread(inputTiff, 1, 'Info', info);
    catch ME
        % Fallback for very large TIFFs where imfinfo allocation fails.
        if verbose
            fprintf('imfinfo failed (%s). Falling back to low-memory TIFF scan.\n', ME.message);
        end
        useInfo = false;
        [nIn, firstFrame] = localCountFramesAndReadFirst(inputTiff);
    end
    inClass = class(firstFrame);
    outClass = outClassReq;
    if strcmp(outClassReq, 'same')
        outClass = inClass;
    end

    halo = max(0, ceil(3 * sigmaZ));

    if exist(outPath, 'file') == 2
        if overwrite
            delete(outPath);
        else
            error('Output TIFF exists and Overwrite=false: %s', outPath);
        end
    end

    wroteAny = false;
    nOut = 0;
    nChunks = 0;
    writer = [];
    writerCleanup = [];

    tStart = tic;
    coreStart = 1;
    while coreStart <= nIn
        coreEnd = min(nIn, coreStart + chunkFrames - 1);
        readStart = max(1, coreStart - halo);
        readEnd = min(nIn, coreEnd + halo);

        vol = localReadVolumeSingle(inputTiff, info, useInfo, readStart, readEnd);
        if sigmaXY > 0 || sigmaZ > 0
            vol = imgaussfilt3(vol, [sigmaXY sigmaXY sigmaZ], 'Padding', 'replicate');
        end

        coreLocalStart = coreStart - readStart + 1;
        coreLocalEnd = coreEnd - readStart + 1;
        coreVol = vol(:,:,coreLocalStart:coreLocalEnd);

        gIdx = coreStart:coreEnd;
        keep = mod(gIdx - 1, ds) == 0;
        outVol = coreVol(:,:,keep);

        for k = 1:size(outVol, 3)
            fr = localCastFrame(outVol(:,:,k), outClass);
            if ~wroteAny
                writer = localOpenTiffWriter(outPath, size(fr,1), size(fr,2), class(fr), compression, useBigTiff);
                writerCleanup = onCleanup(@() localCloseWriter(writer));
                localWriteFrame(writer, fr, true);
                wroteAny = true;
            else
                localWriteFrame(writer, fr, false);
            end
            nOut = nOut + 1;
        end

        nChunks = nChunks + 1;
        if verbose
            fprintf('Chunk %d: in %d-%d (read %d-%d), wrote %d frames\n', ...
                nChunks, coreStart, coreEnd, readStart, readEnd, size(outVol,3));
        end

        coreStart = coreEnd + 1;
    end

    report = struct();
    report.input_tiff = inputTiff;
    report.output_tiff = outPath;
    report.generated_at = datestr(now, 'yyyy-mm-dd HH:MM:SS');
    report.options = p.Results;
    report.input_class = inClass;
    report.output_class = outClass;
    report.n_input_frames = nIn;
    report.n_output_frames = nOut;
    report.n_chunks = nChunks;
    report.elapsed_sec = toc(tStart);

    if ~isempty(writerCleanup)
        clear writerCleanup;
    end

    if verbose
        fprintf('Saved: %s\n', outPath);
        fprintf('Frames in/out: %d -> %d | chunks=%d | time=%.2fs\n', ...
            nIn, nOut, nChunks, report.elapsed_sec);
    end
end

function vol = localReadVolumeSingle(tiffPath, info, useInfo, iStart, iEnd)
    n = iEnd - iStart + 1;
    if useInfo
        fr0 = imread(tiffPath, iStart, 'Info', info);
    else
        fr0 = imread(tiffPath, iStart);
    end
    [h, w] = size(fr0);
    vol = zeros(h, w, n, 'single');
    vol(:,:,1) = single(fr0);
    for ii = 2:n
        if useInfo
            vol(:,:,ii) = single(imread(tiffPath, iStart + ii - 1, 'Info', info));
        else
            vol(:,:,ii) = single(imread(tiffPath, iStart + ii - 1));
        end
    end
end

function [nFrames, firstFrame] = localCountFramesAndReadFirst(tiffPath)
    t = Tiff(tiffPath, 'r');
    c = onCleanup(@() t.close());
    firstFrame = t.read();
    nFrames = 1;
    while ~t.lastDirectory()
        t.nextDirectory();
        nFrames = nFrames + 1;
    end
    clear c;
end

function writer = localOpenTiffWriter(outPath, h, w, frameClass, compressionStr, useBigTiff)
    writer = struct();
    if useBigTiff
        try
            writer.t = Tiff(outPath, 'w8');
        catch
            writer.t = Tiff(outPath, 'w');
        end
    else
        writer.t = Tiff(outPath, 'w');
    end
    writer.tag = localBuildTiffTag(h, w, frameClass, compressionStr);
end

function localWriteFrame(writer, fr, isFirst)
    t = writer.t;
    if ~isFirst
        t.writeDirectory();
    end
    t.setTag(writer.tag);
    t.write(fr);
end

function localCloseWriter(writer)
    try
        if ~isempty(writer) && isfield(writer, 't') && isa(writer.t, 'Tiff')
            writer.t.close();
        end
    catch
    end
end

function tag = localBuildTiffTag(h, w, frameClass, compressionStr)
    tag = struct();
    tag.ImageLength = h;
    tag.ImageWidth = w;
    tag.Photometric = Tiff.Photometric.MinIsBlack;
    tag.SamplesPerPixel = 1;
    % Use smaller strips to avoid large-strip write failures on some stacks.
    tag.RowsPerStrip = max(16, min(h, 128));
    tag.PlanarConfiguration = Tiff.PlanarConfiguration.Chunky;
    tag.Software = 'MATLAB';

    switch lower(frameClass)
        case 'uint8'
            tag.BitsPerSample = 8;
            tag.SampleFormat = Tiff.SampleFormat.UInt;
        case 'uint16'
            tag.BitsPerSample = 16;
            tag.SampleFormat = Tiff.SampleFormat.UInt;
        case 'int16'
            tag.BitsPerSample = 16;
            tag.SampleFormat = Tiff.SampleFormat.Int;
        case 'single'
            tag.BitsPerSample = 32;
            tag.SampleFormat = Tiff.SampleFormat.IEEEFP;
        case 'double'
            tag.BitsPerSample = 64;
            tag.SampleFormat = Tiff.SampleFormat.IEEEFP;
        otherwise
            error('Unsupported frame class for TIFF writing: %s', frameClass);
    end

    c = lower(strtrim(compressionStr));
    switch c
        case {'none', 'uncompressed'}
            tag.Compression = Tiff.Compression.None;
        case 'lzw'
            tag.Compression = Tiff.Compression.LZW;
        case {'deflate', 'adobe'}
            tag.Compression = Tiff.Compression.Deflate;
        otherwise
            error('Unsupported Compression: %s (use none|lzw|deflate)', compressionStr);
    end
end

function fr = localCastFrame(frIn, outClass)
    switch outClass
        case 'uint8'
            fr = uint8(max(0, min(255, round(frIn))));
        case 'uint16'
            fr = uint16(max(0, min(65535, round(frIn))));
        case 'single'
            fr = single(frIn);
        case 'double'
            fr = double(frIn);
        otherwise
            % Fallback for integer source classes (e.g. uint32) if requested by name.
            try
                fr = cast(frIn, outClass);
            catch
                error('Unsupported OutputClass: %s', outClass);
            end
    end
end
