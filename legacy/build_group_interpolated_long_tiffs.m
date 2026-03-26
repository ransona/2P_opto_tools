function reports = build_group_interpolated_long_tiffs(rootDir, varargin)
% build_group_interpolated_long_tiffs
% For each red-FOV group (hardware-valid runs), load each run TIFF, replace
% stim-affected lines by averaging the same pixels from previous and next
% frame, then append corrected runs into one long TIFF per group.
%
% Usage:
%   reports = build_group_interpolated_long_tiffs(rootDir)
%
% Name-value options:
%   'OutputDir'                 default fullfile(rootDir,'group_long_tiffs')
%   'GroupIds'                  default [] (all groups)
%   'Overwrite'                 default true
%   'Verbose'                   default true
%   'SimilarityThreshold'       default 0.92
%   'MinGroupSize'              default 2
%   'RequireExactTriggerCount'  default true
%   'IncludeSingletons'         default false
%
% Output:
%   reports: struct array with per-group processing summary

    if nargin < 1 || isempty(rootDir)
        rootDir = pwd;
    end
    if isstring(rootDir)
        rootDir = char(rootDir);
    end
    validateattributes(rootDir, {'char'}, {'row'});
    assert(exist(rootDir, 'dir') == 7, 'Root folder does not exist: %s', rootDir);

    p = inputParser;
    p.addParameter('OutputDir', '', @(v)ischar(v) || isstring(v));
    p.addParameter('GroupIds', [], @(v)isnumeric(v) && isvector(v));
    p.addParameter('Overwrite', true, @(v)islogical(v) && isscalar(v));
    p.addParameter('Verbose', true, @(v)islogical(v) && isscalar(v));
    p.addParameter('SimilarityThreshold', 0.92, @(v)isnumeric(v) && isscalar(v) && isfinite(v) && v > 0 && v <= 1);
    p.addParameter('MinGroupSize', 2, @(v)isnumeric(v) && isscalar(v) && isfinite(v) && v >= 1);
    p.addParameter('RequireExactTriggerCount', true, @(v)islogical(v) && isscalar(v));
    p.addParameter('IncludeSingletons', false, @(v)islogical(v) && isscalar(v));
    p.parse(varargin{:});

    outputDir = char(p.Results.OutputDir);
    if isempty(outputDir)
        outputDir = fullfile(rootDir, 'group_long_tiffs');
    end
    groupIds = unique(round(p.Results.GroupIds(:).'));
    overwrite = p.Results.Overwrite;
    verbose = p.Results.Verbose;

    if exist(outputDir, 'dir') ~= 7
        [ok, msg] = mkdir(outputDir);
        assert(ok, 'Could not create output directory: %s', msg);
    end

    if verbose
        fprintf('Finding groups...\n');
    end
    [groups, runs, ~, statsFind] = find_same_red_fov_hardware_runs( ...
        rootDir, ...
        'SimilarityThreshold', p.Results.SimilarityThreshold, ...
        'MinGroupSize', p.Results.MinGroupSize, ...
        'RequireExactTriggerCount', p.Results.RequireExactTriggerCount, ...
        'IncludeSingletons', p.Results.IncludeSingletons, ...
        'Verbose', false);

    if isempty(groups)
        reports = struct([]);
        if verbose
            fprintf('No groups found. Nothing to do.\n');
        end
        return;
    end

    if isempty(groupIds)
        selectedIdx = 1:numel(groups);
    else
        selectedIdx = find(ismember([groups.group_id], groupIds));
    end

    if isempty(selectedIdx)
        reports = struct([]);
        if verbose
            fprintf('No selected groups found.\n');
        end
        return;
    end

    reports = repmat(localEmptyGroupReport(), 0, 1);

    for iSel = 1:numel(selectedIdx)
        g = groups(selectedIdx(iSel));
        runIdx = localSortRunIndices(runs, g.member_indices);
        outPath = fullfile(outputDir, sprintf('group_%03d_long_interp.tif', g.group_id));

        if exist(outPath, 'file') && ~overwrite
            if verbose
                fprintf('Skipping group %d (output exists): %s\n', g.group_id, outPath);
            end
            rep = localEmptyGroupReport();
            rep.group_id = g.group_id;
            rep.output_tiff = outPath;
            rep.status = 'skipped_exists';
            rep.n_runs_total = numel(runIdx);
            rep.n_runs_written = 0;
            rep.find_stats = statsFind;
            reports(end+1,1) = rep; %#ok<AGROW>
            continue;
        end

        if exist(outPath, 'file')
            delete(outPath);
        end

        if verbose
            fprintf('\nGroup %d: %d run(s)\n', g.group_id, numel(runIdx));
            fprintf('Output: %s\n', outPath);
        end

        hTif = [];
        writerState = localInitWriterState();
        runReports = repmat(localEmptyRunReport(), 0, 1);
        groupStatus = 'ok';
        groupMessage = '';

        try
            hTif = Tiff(outPath, 'w8');

            for k = 1:numel(runIdx)
                rr = localProcessOneRun(runs(runIdx(k)), hTif, writerState);
                writerState = rr.writer_state_after;
                runReports(end+1,1) = rr; %#ok<AGROW>

                if verbose
                    fprintf('  Run %d/%d: %s\n', k, numel(runIdx), rr.status);
                    fprintf('    MAT: %s\n', rr.mat_file);
                    if ~isempty(rr.tiff_file)
                        fprintf('    TIFF: %s\n', rr.tiff_file);
                    end
                    if ~isempty(rr.message)
                        fprintf('    Note: %s\n', rr.message);
                    end
                end
            end
        catch ME
            groupStatus = 'error';
            groupMessage = ME.message;
            if verbose
                fprintf('Group %d failed: %s\n', g.group_id, ME.message);
            end
        end

        try
            if ~isempty(hTif)
                close(hTif);
            end
        catch
        end

        if ~writerState.has_frame && exist(outPath, 'file')
            delete(outPath);
            if strcmp(groupStatus, 'ok')
                groupStatus = 'no_output';
                if isempty(groupMessage)
                    groupMessage = 'No frames were written for this group.';
                end
            end
        end

        rep = localEmptyGroupReport();
        rep.group_id = g.group_id;
        rep.output_tiff = outPath;
        rep.status = groupStatus;
        rep.message = groupMessage;
        rep.n_runs_total = numel(runIdx);
        rep.n_runs_written = sum(strcmp({runReports.status}, 'written'));
        rep.n_frames_written = sum([runReports.n_frames_written]);
        rep.n_frames_corrected = sum([runReports.n_frames_corrected]);
        rep.n_lines_marked = sum([runReports.n_lines_marked]);
        rep.n_lines_corrected = sum([runReports.n_lines_corrected]);
        rep.n_boundary_lines_skipped = sum([runReports.n_boundary_lines_skipped]);
        rep.n_events_input = sum([runReports.n_events_input]);
        rep.n_events_used = sum([runReports.n_events_used]);
        rep.run_reports = runReports;
        rep.find_stats = statsFind;

        reports(end+1,1) = rep; %#ok<AGROW>
    end

    if verbose
        fprintf('\nDone. Generated %d group output file(s).\n', numel(reports));
        nOk = sum(strcmp({reports.status}, 'ok'));
        fprintf('Groups status ok: %d\n', nOk);
    end
end

function rep = localEmptyGroupReport()
    rep = struct( ...
        'group_id', NaN, ...
        'output_tiff', '', ...
        'status', '', ...
        'message', '', ...
        'n_runs_total', 0, ...
        'n_runs_written', 0, ...
        'n_frames_written', 0, ...
        'n_frames_corrected', 0, ...
        'n_lines_marked', 0, ...
        'n_lines_corrected', 0, ...
        'n_boundary_lines_skipped', 0, ...
        'n_events_input', 0, ...
        'n_events_used', 0, ...
        'run_reports', struct([]), ...
        'find_stats', struct());
end

function rep = localEmptyRunReport()
    rep = struct( ...
        'status', 'skipped', ...
        'message', '', ...
        'mat_file', '', ...
        'tiff_file', '', ...
        'n_frames_in', 0, ...
        'n_frames_written', 0, ...
        'n_frames_corrected', 0, ...
        'n_lines_marked', 0, ...
        'n_lines_corrected', 0, ...
        'n_boundary_lines_skipped', 0, ...
        'n_events_input', 0, ...
        'n_events_used', 0, ...
        'writer_state_after', localInitWriterState());
end

function st = localInitWriterState()
    st = struct( ...
        'has_frame', false, ...
        'expected_size', [], ...
        'expected_class', '');
end

function idxSorted = localSortRunIndices(runs, idx)
    idx = idx(:).';
    if isempty(idx)
        idxSorted = idx;
        return;
    end

    t = nan(size(idx));
    for i = 1:numel(idx)
        s = runs(idx(i)).timestamp;
        if ~ischar(s)
            t(i) = inf;
            continue;
        end
        try
            t(i) = datenum(s, 'yyyy-mm-dd HH:MM:SS');
        catch
            t(i) = inf;
        end
    end

    [~, ord] = sortrows([t(:) (1:numel(idx)).'], [1 2]);
    idxSorted = idx(ord);
end

function rr = localProcessOneRun(runMeta, hTif, writerState)
    rr = localEmptyRunReport();
    rr.mat_file = runMeta.file;
    rr.writer_state_after = writerState;

    S = [];
    try
        S = load(runMeta.file, 'stimData');
    catch ME
        rr.status = 'skipped_load_mat_failed';
        rr.message = ME.message;
        return;
    end
    if ~isfield(S, 'stimData') || ~isstruct(S.stimData) || isempty(S.stimData)
        rr.status = 'skipped_no_stimData';
        rr.message = 'stimData missing';
        return;
    end
    stimData = S.stimData;

    tiffPath = localResolveRunTiffPath(runMeta.file, stimData);
    rr.tiff_file = tiffPath;
    if isempty(tiffPath) || exist(tiffPath, 'file') ~= 2
        rr.status = 'skipped_no_tiff';
        rr.message = 'Could not resolve TIFF path for run.';
        return;
    end

    info = [];
    try
        info = imfinfo(tiffPath);
    catch ME
        rr.status = 'skipped_tiff_info_failed';
        rr.message = ME.message;
        return;
    end
    nFrames = numel(info);
    rr.n_frames_in = nFrames;
    if nFrames < 1
        rr.status = 'skipped_empty_tiff';
        rr.message = 'No frames in TIFF.';
        return;
    end

    try
        firstFrame = imread(tiffPath, 1, 'Info', info);
    catch ME
        rr.status = 'skipped_tiff_read_failed';
        rr.message = ME.message;
        return;
    end

    thisSize = size(firstFrame);
    thisClass = class(firstFrame);
    if rr.writer_state_after.has_frame
        if ~isequal(rr.writer_state_after.expected_size, thisSize)
            rr.status = 'skipped_size_mismatch';
            rr.message = sprintf('Size mismatch. Expected [%s], got [%s].', ...
                num2str(rr.writer_state_after.expected_size), num2str(thisSize));
            return;
        end
        if ~strcmp(rr.writer_state_after.expected_class, thisClass)
            rr.status = 'skipped_class_mismatch';
            rr.message = sprintf('Class mismatch. Expected %s, got %s.', ...
                rr.writer_state_after.expected_class, thisClass);
            return;
        end
    end

    [lineMask, evInfo] = localBuildAffectedLineMask(stimData, nFrames, size(firstFrame, 1));
    rr.n_events_input = evInfo.n_events_input;
    rr.n_events_used = evInfo.n_events_used;
    rr.n_lines_marked = evInfo.n_lines_marked;

    prev = [];
    curr = firstFrame;
    if nFrames > 1
        next = imread(tiffPath, 2, 'Info', info);
    else
        next = [];
    end

    nFramesWritten = 0;
    nFramesCorrected = 0;
    nLinesCorrected = 0;
    nBoundarySkipped = 0;

    for f = 1:nFrames
        out = curr;
        if ~isempty(lineMask)
            rows = lineMask(:, f);
        else
            rows = false(size(curr, 1), 1);
        end

        if any(rows)
            if f > 1 && f < nFrames
                out = localInterpolateRowsFromNeighbors(prev, curr, next, rows);
                nFramesCorrected = nFramesCorrected + 1;
                nLinesCorrected = nLinesCorrected + nnz(rows);
            else
                nBoundarySkipped = nBoundarySkipped + nnz(rows);
            end
        end

        rr.writer_state_after = localAppendFrame(hTif, rr.writer_state_after, out);
        nFramesWritten = nFramesWritten + 1;

        prev = curr;
        curr = next;
        if f + 1 < nFrames
            next = imread(tiffPath, f + 2, 'Info', info);
        else
            next = [];
        end
    end

    rr.n_frames_written = nFramesWritten;
    rr.n_frames_corrected = nFramesCorrected;
    rr.n_lines_corrected = nLinesCorrected;
    rr.n_boundary_lines_skipped = nBoundarySkipped;
    rr.status = 'written';
end

function out = localInterpolateRowsFromNeighbors(prev, curr, next, rowMask)
    out = curr;
    if ~any(rowMask) || isempty(prev) || isempty(next)
        return;
    end
    prevRows = double(prev(rowMask, :, :));
    nextRows = double(next(rowMask, :, :));
    avgRows = (prevRows + nextRows) / 2;
    out(rowMask, :, :) = localCastLike(avgRows, class(curr));
end

function out = localCastLike(v, className)
    switch className
        case {'uint8','uint16','uint32','uint64','int8','int16','int32','int64'}
            out = cast(round(v), className);
        otherwise
            out = cast(v, className);
    end
end

function state = localAppendFrame(hTif, state, frame)
    if ~state.has_frame
        state.expected_size = size(frame);
        state.expected_class = class(frame);
    end

    if ~isequal(size(frame), state.expected_size)
        error('Frame size mismatch within output stream.');
    end
    if ~strcmp(class(frame), state.expected_class)
        error('Frame class mismatch within output stream.');
    end

    if state.has_frame
        hTif.writeDirectory();
    end
    [tagstruct, sampleFormat] = localTagStructForFrame(frame);
    setTag(hTif, tagstruct);
    setTag(hTif, 'SampleFormat', sampleFormat);
    hTif.write(frame);
    state.has_frame = true;
end

function [tagstruct, sampleFormat] = localTagStructForFrame(frame)
    imgSize = size(frame);
    if numel(imgSize) < 3
        spp = 1;
    else
        spp = imgSize(3);
    end

    bits = localBitsPerSample(class(frame));
    if spp > 1
        bitsTag = repmat(bits, 1, spp);
        photometric = Tiff.Photometric.RGB;
    else
        bitsTag = bits;
        photometric = Tiff.Photometric.MinIsBlack;
    end

    tagstruct = struct();
    tagstruct.ImageLength = imgSize(1);
    tagstruct.ImageWidth = imgSize(2);
    tagstruct.Photometric = photometric;
    tagstruct.BitsPerSample = bitsTag;
    tagstruct.SamplesPerPixel = spp;
    tagstruct.RowsPerStrip = imgSize(1);
    tagstruct.Compression = Tiff.Compression.None;
    tagstruct.PlanarConfiguration = Tiff.PlanarConfiguration.Chunky;
    tagstruct.Software = 'build_group_interpolated_long_tiffs';
    sampleFormat = localSampleFormatForClass(class(frame));
end

function bits = localBitsPerSample(className)
    switch className
        case {'uint8','int8'}
            bits = 8;
        case {'uint16','int16'}
            bits = 16;
        case {'uint32','int32','single'}
            bits = 32;
        case {'uint64','int64','double'}
            bits = 64;
        otherwise
            error('Unsupported frame class: %s', className);
    end
end

function sf = localSampleFormatForClass(className)
    switch className
        case {'uint8','uint16','uint32','uint64'}
            sf = Tiff.SampleFormat.UInt;
        case {'int8','int16','int32','int64'}
            sf = Tiff.SampleFormat.Int;
        case {'single','double'}
            sf = Tiff.SampleFormat.IEEEFP;
        otherwise
            error('Unsupported frame class: %s', className);
    end
end

function [lineMask, info] = localBuildAffectedLineMask(stimData, nFrames, nLines)
    lineMask = false(nLines, nFrames);
    info = struct('n_events_input', 0, 'n_events_used', 0, 'n_lines_marked', 0);

    f0 = localGetHwField(stimData, 'frame_index');
    l0 = localGetHwField(stimData, 'line_index');
    f1 = localGetHwField(stimData, 'end_frame_index');
    l1 = localGetHwField(stimData, 'end_line_index');
    spans = localGetHwField(stimData, 'spans_multiple_frames');

    if isempty(f0) || isempty(l0)
        return;
    end

    n = min(numel(f0), numel(l0));
    info.n_events_input = n;
    if n <= 0
        return;
    end

    used = 0;
    for i = 1:n
        sF = round(double(f0(i)));
        sL = round(double(l0(i)));
        if ~(isfinite(sF) && isfinite(sL))
            continue;
        end

        if i <= numel(f1) && isfinite(f1(i))
            eF = round(double(f1(i)));
        else
            eF = sF;
        end
        if i <= numel(l1) && isfinite(l1(i))
            eL = round(double(l1(i)));
        else
            eL = sL;
        end

        if i <= numel(spans) && localScalarLogical(spans(i)) && eF == sF
            eF = sF + 1;
        end

        if ~isfinite(eF) || ~isfinite(eL)
            continue;
        end

        if eF < sF
            tf = sF; sF = eF; eF = tf;
            tl = sL; sL = eL; eL = tl;
        end

        sF = max(1, min(nFrames, sF));
        eF = max(1, min(nFrames, eF));
        sL = max(1, min(nLines, sL));
        eL = max(1, min(nLines, eL));

        if sF == eF
            lMin = min(sL, eL);
            lMax = max(sL, eL);
            lineMask(lMin:lMax, sF) = true;
        else
            lineMask(sL:nLines, sF) = true;
            if eF > sF + 1
                lineMask(:, sF+1:eF-1) = true;
            end
            lineMask(1:eL, eF) = true;
        end
        used = used + 1;
    end

    info.n_events_used = used;
    info.n_lines_marked = nnz(lineMask);
end

function x = localGetHwField(stimData, key)
    x = [];
    switch key
        case 'frame_index'
            fieldA = 'stim_trigger_times_hw_frame_index';
            fieldB = 'hardware_frame_index';
        case 'line_index'
            fieldA = 'stim_trigger_times_hw_line_index';
            fieldB = 'hardware_line_index';
        case 'end_frame_index'
            fieldA = 'stim_trigger_times_hw_end_frame_index';
            fieldB = 'hardware_end_frame_index';
        case 'end_line_index'
            fieldA = 'stim_trigger_times_hw_end_line_index';
            fieldB = 'hardware_end_line_index';
        case 'spans_multiple_frames'
            fieldA = 'stim_trigger_times_hw_spans_multiple_frames';
            fieldB = 'hardware_spans_multiple_frames';
        otherwise
            fieldA = '';
            fieldB = '';
    end

    if ~isempty(fieldA) && isfield(stimData, fieldA) && ~isempty(stimData.(fieldA))
        x = stimData.(fieldA)(:);
        return;
    end

    if isfield(stimData, 'stim_timing') && isstruct(stimData.stim_timing) && ...
            ~isempty(fieldB) && isfield(stimData.stim_timing, fieldB) && ...
            ~isempty(stimData.stim_timing.(fieldB))
        x = stimData.stim_timing.(fieldB)(:);
    end
end

function tiffPath = localResolveRunTiffPath(matPath, stimData)
    tiffPath = '';

    if isfield(stimData, 'green_tiff_path') && ~isempty(stimData.green_tiff_path)
        p = stimData.green_tiff_path;
        if isstring(p) && isscalar(p)
            p = char(p);
        end
        if ischar(p) && ~isempty(p) && exist(p, 'file') == 2
            tiffPath = p;
            return;
        end
    end

    [matDir, matBase] = fileparts(matPath);
    cands = { ...
        fullfile(matDir, [matBase '.tif']), ...
        fullfile(matDir, [matBase '.tiff']), ...
        fullfile(matDir, [matBase '_green.tif']), ...
        fullfile(matDir, [matBase '_green.tiff'])};

    for i = 1:numel(cands)
        if exist(cands{i}, 'file') == 2
            tiffPath = cands{i};
            return;
        end
    end
end

function tf = localScalarLogical(v)
    tf = false;
    if isempty(v)
        return;
    end
    if islogical(v)
        tf = any(v(:));
        return;
    end
    if isnumeric(v)
        tf = any(v(:) ~= 0);
        return;
    end
    if ischar(v)
        s = strtrim(lower(v));
        tf = ~(isempty(s) || strcmp(s, 'false') || strcmp(s, '0') || strcmp(s, 'off') || strcmp(s, 'no'));
        return;
    end
    if isstring(v)
        if isscalar(v)
            tf = localScalarLogical(char(v));
        else
            tf = any(v ~= "");
        end
    end
end
