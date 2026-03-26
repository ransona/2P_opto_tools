function annotations = build_group_frame_stim_annotations(rootDir, varargin)
% build_group_frame_stim_annotations
% Build per-frame stimulation annotations for each red-FOV group and save to
% a MAT file. This script does not write/modify TIFF files.
%
% For each concatenated group frame, stores:
%   - whether stimulation occurred
%   - stimulated cell coordinate(s) in um
%
% Usage:
%   annotations = build_group_frame_stim_annotations(rootDir)
%
% Name-value options:
%   'OutputMat'                 default fullfile(rootDir,'group_frame_stim_annotations.mat')
%   'GroupIds'                  default [] (all)
%   'SimilarityThreshold'       default 0.92
%   'MinGroupSize'              default 5
%   'RequireExactTriggerCount'  default true
%   'IncludeSingletons'         default false
%   'Verbose'                   default true

    if nargin < 1 || isempty(rootDir)
        rootDir = pwd;
    end
    if isstring(rootDir)
        rootDir = char(rootDir);
    end
    validateattributes(rootDir, {'char'}, {'row'});
    assert(exist(rootDir, 'dir') == 7, 'Root folder does not exist: %s', rootDir);

    p = inputParser;
    p.addParameter('OutputMat', '', @(v)ischar(v) || isstring(v));
    p.addParameter('GroupIds', [], @(v)isnumeric(v) && isvector(v));
    p.addParameter('SimilarityThreshold', 0.92, @(v)isnumeric(v) && isscalar(v) && isfinite(v) && v > 0 && v <= 1);
    p.addParameter('MinGroupSize', 5, @(v)isnumeric(v) && isscalar(v) && isfinite(v) && v >= 1);
    p.addParameter('RequireExactTriggerCount', true, @(v)islogical(v) && isscalar(v));
    p.addParameter('IncludeSingletons', false, @(v)islogical(v) && isscalar(v));
    p.addParameter('Verbose', true, @(v)islogical(v) && isscalar(v));
    p.parse(varargin{:});

    outputMat = char(p.Results.OutputMat);
    if isempty(outputMat)
        outputMat = fullfile(rootDir, 'group_frame_stim_annotations.mat');
    end
    groupIds = unique(round(p.Results.GroupIds(:).'));
    verbose = p.Results.Verbose;

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
        annotations = localEmptyAnnotations(rootDir, outputMat, statsFind);
        save(outputMat, 'annotations', '-v7.3');
        if verbose
            fprintf('No groups found. Wrote empty annotation file:\n  %s\n', outputMat);
        end
        return;
    end

    if isempty(groupIds)
        selectedIdx = 1:numel(groups);
    else
        selectedIdx = find(ismember([groups.group_id], groupIds));
    end

    annotations = localEmptyAnnotations(rootDir, outputMat, statsFind);
    annotations.options = p.Results;
    annotations.groups = repmat(localEmptyGroupAnnotation(), 0, 1);

    for iSel = 1:numel(selectedIdx)
        g = groups(selectedIdx(iSel));
        runIdx = localSortRunIndices(runs, g.member_indices);

        grp = localEmptyGroupAnnotation();
        grp.group_id = g.group_id;
        grp.run_indices = runIdx;
        grp.run_mat_files = {runs(runIdx).file};
        grp.runs = repmat(localEmptyRunAnnotation(), 0, 1);

        if verbose
            fprintf('\nGroup %d: %d run(s)\n', grp.group_id, numel(runIdx));
        end

        allStimMask = false(0,1);
        allCenters = cell(0,1);
        allFrameRunOrdinal = zeros(0,1);
        allFrameInRun = zeros(0,1);
        runFrameRanges = nan(numel(runIdx), 2);
        frameOffset = 0;

        for k = 1:numel(runIdx)
            runMeta = runs(runIdx(k));
            rr = localBuildRunAnnotation(runMeta);
            rr.run_ordinal = k;

            if rr.n_frames > 0
                startG = frameOffset + 1;
                stopG = frameOffset + rr.n_frames;
                runFrameRanges(k,:) = [startG stopG];
                frameOffset = stopG;

                allStimMask = [allStimMask; rr.frame_has_stim(:)]; %#ok<AGROW>
                allCenters = [allCenters; rr.frame_centers_um(:)]; %#ok<AGROW>
                allFrameRunOrdinal = [allFrameRunOrdinal; repmat(k, rr.n_frames, 1)]; %#ok<AGROW>
                allFrameInRun = [allFrameInRun; (1:rr.n_frames).']; %#ok<AGROW>
            end

            grp.runs(end+1,1) = rr; %#ok<AGROW>

            if verbose
                fprintf('  Run %d/%d: %s, frames=%d, stimFrames=%d\n', ...
                    k, numel(runIdx), rr.status, rr.n_frames, rr.n_frames_with_stim);
                if ~isempty(rr.message)
                    fprintf('    Note: %s\n', rr.message);
                end
            end
        end

        grp.n_runs = numel(grp.runs);
        grp.n_frames_total = numel(allStimMask);
        grp.n_frames_with_stim = nnz(allStimMask);
        grp.frame_has_stim = allStimMask;
        grp.frame_centers_um = allCenters;
        grp.frame_center_count = cellfun(@(x)size(x,1), allCenters);
        grp.frame_run_ordinal = allFrameRunOrdinal;
        grp.frame_index_in_run = allFrameInRun;
        grp.run_frame_ranges_global = runFrameRanges;

        singleCenter = nan(grp.n_frames_total, 2);
        for iF = 1:grp.n_frames_total
            c = grp.frame_centers_um{iF};
            if size(c,1) == 1
                singleCenter(iF,:) = c;
            end
        end
        grp.frame_single_center_um = singleCenter;

        annotations.groups(end+1,1) = grp; %#ok<AGROW>
    end

    annotations.n_groups = numel(annotations.groups);
    annotations.generated_at = datestr(now, 'yyyy-mm-dd HH:MM:SS');

    save(outputMat, 'annotations', '-v7.3');
    if verbose
        fprintf('\nSaved annotations MAT:\n  %s\n', outputMat);
    end
end

function out = localEmptyAnnotations(rootDir, outputMat, statsFind)
    out = struct();
    out.root_dir = rootDir;
    out.output_mat = outputMat;
    out.generated_at = datestr(now, 'yyyy-mm-dd HH:MM:SS');
    out.options = struct();
    out.find_stats = statsFind;
    out.n_groups = 0;
    out.groups = struct([]);
end

function grp = localEmptyGroupAnnotation()
    grp = struct( ...
        'group_id', NaN, ...
        'run_indices', [], ...
        'run_mat_files', {{}}, ...
        'n_runs', 0, ...
        'n_frames_total', 0, ...
        'n_frames_with_stim', 0, ...
        'frame_has_stim', false(0,1), ...
        'frame_centers_um', {cell(0,1)}, ...
        'frame_center_count', zeros(0,1), ...
        'frame_single_center_um', nan(0,2), ...
        'frame_run_ordinal', zeros(0,1), ...
        'frame_index_in_run', zeros(0,1), ...
        'run_frame_ranges_global', nan(0,2), ...
        'runs', struct([]));
end

function rr = localEmptyRunAnnotation()
    rr = struct( ...
        'run_ordinal', NaN, ...
        'status', 'skipped', ...
        'message', '', ...
        'mat_file', '', ...
        'tiff_files', {{}}, ...
        'n_frames_per_tiff', zeros(0,1), ...
        'n_frames', 0, ...
        'n_events_input', 0, ...
        'n_events_used', 0, ...
        'n_frames_with_stim', 0, ...
        'frame_has_stim', false(0,1), ...
        'frame_centers_um', {cell(0,1)});
end

function rr = localBuildRunAnnotation(runMeta)
    rr = localEmptyRunAnnotation();
    rr.mat_file = runMeta.file;

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
        rr.message = 'stimData missing.';
        return;
    end
    stimData = S.stimData;

    tiffFiles = localResolveRunTiffFiles(runMeta.file, stimData);
    rr.tiff_files = tiffFiles;
    if isempty(tiffFiles)
        rr.status = 'skipped_no_tiff';
        rr.message = 'No TIFF files resolved for this run.';
        return;
    end

    nFramesPer = zeros(numel(tiffFiles),1);
    for i = 1:numel(tiffFiles)
        try
            info = imfinfo(tiffFiles{i});
            nFramesPer(i) = numel(info);
        catch
            nFramesPer(i) = 0;
        end
    end
    rr.n_frames_per_tiff = nFramesPer;
    rr.n_frames = sum(nFramesPer);
    if rr.n_frames <= 0
        rr.status = 'skipped_empty_tiff';
        rr.message = 'Resolved TIFF files contain zero readable frames.';
        return;
    end

    [stimMask, centersPerFrame, evInfo] = localBuildFrameStimFromHardware(stimData, rr.n_frames);
    rr.frame_has_stim = stimMask;
    rr.frame_centers_um = centersPerFrame;
    rr.n_frames_with_stim = nnz(stimMask);
    rr.n_events_input = evInfo.n_events_input;
    rr.n_events_used = evInfo.n_events_used;
    rr.status = 'ok';
end

function [stimMask, centersPerFrame, info] = localBuildFrameStimFromHardware(stimData, nFrames)
    stimMask = false(nFrames,1);
    centersPerFrame = repmat({zeros(0,2)}, nFrames, 1);
    info = struct('n_events_input',0,'n_events_used',0);

    frameStart = localGetHwField(stimData, 'frame_index');
    frameEnd = localGetHwField(stimData, 'end_frame_index');
    spans = localGetHwField(stimData, 'spans_multiple_frames');
    if isempty(frameStart)
        return;
    end

    nEvt = numel(frameStart);
    info.n_events_input = nEvt;
    used = 0;
    for i = 1:nEvt
        sF = localScalarFinite(frameStart(i), NaN);
        if ~isfinite(sF)
            continue;
        end
        sF = round(sF);

        if i <= numel(frameEnd)
            eF = localScalarFinite(frameEnd(i), sF);
            eF = round(eF);
        else
            eF = sF;
        end

        if i <= numel(spans) && localScalarLogical(spans(i)) && eF == sF
            eF = sF + 1;
        end

        if ~isfinite(eF)
            eF = sF;
        end
        if eF < sF
            tmp = sF;
            sF = eF;
            eF = tmp;
        end

        sF = max(1, min(nFrames, sF));
        eF = max(1, min(nFrames, eF));

        center = localResolveEventCenterUm(stimData, i);
        for f = sF:eF
            stimMask(f) = true;
            centersPerFrame{f}(end+1,:) = center; %#ok<AGROW>
        end
        used = used + 1;
    end
    info.n_events_used = used;

    for f = 1:nFrames
        c = centersPerFrame{f};
        if isempty(c)
            continue;
        end
        centersPerFrame{f} = unique(round(c, 6), 'rows', 'stable');
    end
end

function center = localResolveEventCenterUm(stimData, iEvt)
    center = [NaN NaN];

    perTrig = [];
    if isfield(stimData, 'stim_per_trigger') && ~isempty(stimData.stim_per_trigger)
        perTrig = stimData.stim_per_trigger;
    end

    if ~isempty(perTrig) && iEvt <= numel(perTrig)
        e = perTrig(iEvt);
        if isstruct(e)
            if isfield(e, 'center_x_um') && isfield(e, 'center_y_um')
                cx = localScalarFinite(e.center_x_um, NaN);
                cy = localScalarFinite(e.center_y_um, NaN);
                if isfinite(cx) && isfinite(cy)
                    center = [cx cy];
                    return;
                end
            end
            if isfield(e, 'center_um') && ~isempty(e.center_um)
                c = double(e.center_um(:).');
                if numel(c) >= 2 && all(isfinite(c(1:2)))
                    center = c(1:2);
                    return;
                end
            end
            if isfield(e, 'center_index') && isfield(stimData, 'centers_um') && ~isempty(stimData.centers_um)
                idx = localScalarFinite(e.center_index, NaN);
                if isfinite(idx)
                    idx = round(idx);
                    cu = stimData.centers_um;
                    if isnumeric(cu) && size(cu,2) >= 2 && idx >= 1 && idx <= size(cu,1)
                        c = double(cu(idx,1:2));
                        if all(isfinite(c))
                            center = c;
                            return;
                        end
                    end
                end
            end
        end
    end

    if isfield(stimData, 'centers_um') && isnumeric(stimData.centers_um) && size(stimData.centers_um,2) >= 2
        cu = double(stimData.centers_um);
        if size(cu,1) == 1 && all(isfinite(cu(1,1:2)))
            center = cu(1,1:2);
        end
    end
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

function files = localResolveRunTiffFiles(matPath, stimData)
    files = {};

    if isfield(stimData, 'green_tiff_path') && ~isempty(stimData.green_tiff_path)
        p = stimData.green_tiff_path;
        if isstring(p) && isscalar(p)
            p = char(p);
        end
        if ischar(p) && ~isempty(p)
            files = localExpandChunkedTiffPath(p);
            if ~isempty(files)
                return;
            end
        end
    end

    [matDir, matBase] = fileparts(matPath);
    baseCandidates = { ...
        fullfile(matDir, [matBase '.tif']), ...
        fullfile(matDir, [matBase '.tiff']), ...
        fullfile(matDir, [matBase '_green.tif']), ...
        fullfile(matDir, [matBase '_green.tiff'])};
    for i = 1:numel(baseCandidates)
        c = localExpandChunkedTiffPath(baseCandidates{i});
        if ~isempty(c)
            files = c;
            return;
        end
    end

    wild = [ ...
        dir(fullfile(matDir, [matBase '*.tif'])); ...
        dir(fullfile(matDir, [matBase '*.tiff']))];
    if isempty(wild)
        return;
    end
    wild = wild(~[wild.isdir]);
    if isempty(wild)
        return;
    end
    tmp = cell(numel(wild),1);
    for i = 1:numel(wild)
        tmp{i} = fullfile(wild(i).folder, wild(i).name);
    end
    files = localSortChunkedPaths(unique(tmp, 'stable'));
end

function files = localExpandChunkedTiffPath(pathOne)
    files = {};
    if isempty(pathOne) || exist(pathOne, 'file') ~= 2
        return;
    end

    [fDir, fBase, fExt] = fileparts(pathOne);
    tok = regexp(fBase, '^(.*?)(?:_)?(\d+)$', 'tokens', 'once');
    if isempty(tok)
        files = {pathOne};
        return;
    end

    stem = tok{1};
    patt = fullfile(fDir, sprintf('%s_*.%s', stem, strrep(fExt,'.','')));
    d = dir(patt);
    if isempty(d)
        files = {pathOne};
        return;
    end
    d = d(~[d.isdir]);
    cand = cell(numel(d),1);
    for i = 1:numel(d)
        cand{i} = fullfile(d(i).folder, d(i).name);
    end
    files = localSortChunkedPaths(cand);
    if isempty(files)
        files = {pathOne};
    end
end

function files = localSortChunkedPaths(filesIn)
    filesIn = filesIn(:);
    if isempty(filesIn)
        files = {};
        return;
    end

    keys = nan(numel(filesIn),1);
    names = cell(numel(filesIn),1);
    for i = 1:numel(filesIn)
        [~, b, ~] = fileparts(filesIn{i});
        names{i} = lower(b);
        t = regexp(b, '(?:_|-)?(\d+)$', 'tokens', 'once');
        if ~isempty(t)
            keys(i) = str2double(t{1});
        else
            keys(i) = inf;
        end
    end

    ordTbl = table(names(:), keys(:), (1:numel(filesIn)).', 'VariableNames', {'name','key','idx'});
    ordTbl = sortrows(ordTbl, {'name','key','idx'});
    files = filesIn(ordTbl.idx);
end

function x = localGetHwField(stimData, key)
    x = [];
    switch key
        case 'frame_index'
            fieldA = 'stim_trigger_times_hw_frame_index';
            fieldB = 'hardware_frame_index';
        case 'end_frame_index'
            fieldA = 'stim_trigger_times_hw_end_frame_index';
            fieldB = 'hardware_end_frame_index';
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

function v = localScalarFinite(x, defaultValue)
    if nargin < 2
        defaultValue = NaN;
    end
    v = defaultValue;
    if isempty(x)
        return;
    end
    if ~isnumeric(x)
        try
            x = double(x);
        catch
            return;
        end
    end
    x = x(:);
    x = x(isfinite(x));
    if isempty(x)
        return;
    end
    v = x(1);
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
