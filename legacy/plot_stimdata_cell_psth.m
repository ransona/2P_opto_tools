function report = plot_stimdata_cell_psth(rootDir, varargin)
% plot_stimdata_cell_psth
% Build per-cell PSTHs directly from stimData MAT files.
% For each trigger, interpolate stimulation-affected frame(s), extract a
% frame-window segment, optionally z-score that segment, then average across trials.
%
% Usage:
%   report = plot_stimdata_cell_psth('F:\Stim_data');
%
% Name-value options:
%   'MatFiles'            : default {} (auto-find .mat under rootDir)
%   'WindowFrames'        : default [-30 90]
%   'OutputDir'           : default fullfile(rootDir,'stimdata_psth')
%   'SaveFigures'         : default true
%   'SavePerCellFigures'  : default true
%   'ZScoreSegments'      : default false
%   'Visible'             : default false
%   'Verbose'             : default true
%
% Output:
%   report struct with per-file/per-cell PSTH data.

    if nargin < 1 || isempty(rootDir)
        rootDir = pwd;
    end
    if isstring(rootDir), rootDir = char(rootDir); end
    assert(exist(rootDir, 'dir') == 7, 'Root folder not found: %s', rootDir);

    p = inputParser;
    p.addParameter('MatFiles', {}, @(v)iscell(v) || isstring(v) || ischar(v));
    p.addParameter('WindowFrames', [-30 90], @(v)isnumeric(v) && numel(v)==2 && all(isfinite(v)));
    p.addParameter('OutputDir', '', @(v)ischar(v) || isstring(v));
    p.addParameter('SaveFigures', true, @(v)islogical(v) && isscalar(v));
    p.addParameter('SavePerCellFigures', true, @(v)islogical(v) && isscalar(v));
    p.addParameter('ZScoreSegments', false, @(v)islogical(v) && isscalar(v));
    p.addParameter('Visible', false, @(v)islogical(v) && isscalar(v));
    p.addParameter('Verbose', true, @(v)islogical(v) && isscalar(v));
    p.parse(varargin{:});

    winF = round(double(p.Results.WindowFrames(:).'));
    assert(winF(2) > winF(1), 'WindowFrames must be [start end] with end > start.');
    nW = winF(2) - winF(1) + 1;
    outDir = char(p.Results.OutputDir);
    if isempty(outDir)
        outDir = fullfile(rootDir, 'stimdata_psth');
    end
    saveFigs = p.Results.SaveFigures;
    saveCellFigs = p.Results.SavePerCellFigures;
    zScoreSegments = p.Results.ZScoreSegments;
    figVisible = p.Results.Visible;
    verbose = p.Results.Verbose;

    matFiles = localResolveMatFiles(rootDir, p.Results.MatFiles);
    if isempty(matFiles)
        error('No MAT files found.');
    end

    if (saveFigs || saveCellFigs) && exist(outDir, 'dir') ~= 7
        [ok, msg] = mkdir(outDir);
        assert(ok, 'Could not create output folder: %s', msg);
    end

    report = struct();
    report.root_dir = rootDir;
    report.output_dir = outDir;
    report.generated_at = datestr(now, 'yyyy-mm-dd HH:MM:SS');
    report.window_frames = winF;
    report.files = repmat(localEmptyFileResult(), 0, 1);

    for iFile = 1:numel(matFiles)
        matPath = matFiles{iFile};
        fr = localProcessOneFile(rootDir, matPath, winF, nW, saveFigs, saveCellFigs, zScoreSegments, outDir, figVisible, verbose);
        report.files(end+1,1) = fr; %#ok<AGROW>
    end
end

function fr = localProcessOneFile(rootDir, matPath, winF, nW, saveFigs, saveCellFigs, zScoreSegments, outDir, figVisible, verbose)
    fr = localEmptyFileResult();
    fr.mat_file = matPath;

    try
        S = load(matPath, 'stimData');
    catch ME
        fr.status = 'load_failed';
        fr.message = ME.message;
        return;
    end
    if ~isfield(S, 'stimData') || ~isstruct(S.stimData) || isempty(S.stimData)
        fr.status = 'missing_stimData';
        fr.message = 'No stimData struct in MAT.';
        return;
    end
    stimData = S.stimData;

    traces = {};
    if isfield(stimData, 'traces') && ~isempty(stimData.traces)
        traces = stimData.traces;
    elseif isfield(stimData, 'trace') && ~isempty(stimData.trace)
        traces = {stimData.trace};
    end
    if isempty(traces)
        fr.status = 'no_traces';
        fr.message = 'No traces in stimData.';
        return;
    end

    [startFrames, endFrames, spanFlags, source] = localGetHardwareFrameTriggers(stimData);
    fr.trigger_source = source;
    fr.n_triggers_total = numel(startFrames);
    if isempty(startFrames)
        fr.status = 'no_hardware_triggers';
        fr.message = 'No hardware frame triggers found.';
        return;
    end

    perTrig = [];
    if isfield(stimData, 'stim_per_trigger') && ~isempty(stimData.stim_per_trigger)
        perTrig = stimData.stim_per_trigger;
    end

    cellRes = repmat(localEmptyCellResult(), 0, 1);
    for iTrace = 1:numel(traces)
        tr = traces{iTrace};
        if ~isstruct(tr) || ~isfield(tr, 'values') || isempty(tr.values)
            continue;
        end
        y = double(tr.values(:));
        nFrames = numel(y);
        if nFrames < (nW + 10)
            continue;
        end

        thisCenterIdx = localTraceCenterIndex(tr, iTrace, stimData);
        trigIdx = localTriggerSelectionForCenter(perTrig, thisCenterIdx, numel(startFrames));
        if isempty(trigIdx)
            continue;
        end

        trials = nan(numel(trigIdx), nW);
        kept = false(numel(trigIdx),1);
        for k = 1:numel(trigIdx)
            j = trigIdx(k);
            sF = startFrames(j);
            eF = endFrames(j);
            if ~isfinite(sF), continue; end
            if ~isfinite(eF), eF = sF; end
            sF = round(sF);
            eF = round(eF);
            if eF < sF
                t = sF; sF = eF; eF = t;
            end
            if spanFlags(j) && eF == sF
                eF = sF + 1;
            end
            segIdx = (sF + winF(1)):(sF + winF(2));
            if segIdx(1) < 1 || segIdx(end) > nFrames
                continue;
            end
            seg = y(segIdx);

            % Interpolate artifact frame(s) in local segment coordinates.
            stimRelStart = 1 - winF(1); % frame sF
            stimRelEnd = stimRelStart + max(0, eF - sF);
            seg = localInterpolateSegmentFrames(seg, stimRelStart, stimRelEnd);

            if zScoreSegments
                seg = localZscoreSegment(seg);
            end
            trials(k,:) = seg;
            kept(k) = true;
        end

        trials = trials(kept,:);
        if isempty(trials)
            continue;
        end

        cr = localEmptyCellResult();
        cr.trace_index = iTrace;
        cr.center_index = thisCenterIdx;
        cr.n_trials = size(trials,1);
        cr.window_frames = winF(1):winF(2);
        cr.trials = trials;
        cr.mean = mean(trials, 1, 'omitnan');
        cr.sem = std(trials, 0, 1, 'omitnan') ./ max(1, sqrt(size(trials,1)));
        cellRes(end+1,1) = cr; %#ok<AGROW>
    end

    if isempty(cellRes)
        fr.status = 'no_cells_with_trials';
        fr.message = 'No trace had valid trigger-aligned trials.';
        return;
    end

    fr.status = 'ok';
    fr.n_cells = numel(cellRes);
    fr.cells = cellRes;
    tag = localPathTag(rootDir, matPath);

    if saveFigs
        figPath = fullfile(outDir, sprintf('%s_psth_per_cell.png', tag));
        localPlotFilePsthFigure(figPath, cellRes, winF, matPath, figVisible);
        fr.figure_path = figPath;
    end
    if saveCellFigs
        for iC = 1:numel(cellRes)
            c = cellRes(iC);
            cellPath = fullfile(outDir, sprintf('%s_cell_%03d_trace_%03d_psth.png', ...
                tag, c.center_index, c.trace_index));
            localPlotSingleCellPsthFigure(cellPath, c, matPath, figVisible);
            cellRes(iC).figure_path = cellPath;
        end
        fr.cells = cellRes;
    end

    if verbose
        fprintf('PSTH: %s | cells=%d | triggers=%d\n', matPath, fr.n_cells, fr.n_triggers_total);
    end
end

function tag = localPathTag(rootDir, matPath)
    matPath = char(matPath);
    rootDir = char(rootDir);
    rel = strrep(matPath, [rootDir filesep], '');
    if strcmp(rel, matPath)
        [~, rel, ext] = fileparts(matPath);
        rel = [rel ext];
    end
    rel = strrep(rel, '/', '__');
    rel = strrep(rel, '\', '__');
    [~, tag, ~] = fileparts(rel);
    if isempty(tag)
        [~, tag, ~] = fileparts(matPath);
    end
    tag = regexprep(tag, '[^A-Za-z0-9_.-]', '_');
end

function [startFrames, endFrames, spanFlags, source] = localGetHardwareFrameTriggers(stimData)
    startFrames = [];
    endFrames = [];
    spanFlags = [];
    source = 'none';

    if isfield(stimData, 'stim_trigger_times_hw_frame_index') && ~isempty(stimData.stim_trigger_times_hw_frame_index)
        startFrames = double(stimData.stim_trigger_times_hw_frame_index(:));
        source = 'stim_trigger_times_hw_frame_index';
    elseif isfield(stimData, 'stim_timing') && isstruct(stimData.stim_timing) && ...
            isfield(stimData.stim_timing, 'hardware_frame_index') && ~isempty(stimData.stim_timing.hardware_frame_index)
        startFrames = double(stimData.stim_timing.hardware_frame_index(:));
        source = 'stim_timing.hardware_frame_index';
    end
    if isempty(startFrames)
        return;
    end
    startFrames = startFrames(isfinite(startFrames));

    if isfield(stimData, 'stim_trigger_times_hw_end_frame_index') && ~isempty(stimData.stim_trigger_times_hw_end_frame_index)
        endFrames = double(stimData.stim_trigger_times_hw_end_frame_index(:));
    elseif isfield(stimData, 'stim_timing') && isstruct(stimData.stim_timing) && ...
            isfield(stimData.stim_timing, 'hardware_end_frame_index') && ~isempty(stimData.stim_timing.hardware_end_frame_index)
        endFrames = double(stimData.stim_timing.hardware_end_frame_index(:));
    end
    if isempty(endFrames)
        endFrames = startFrames;
    end

    n = min(numel(startFrames), numel(endFrames));
    startFrames = startFrames(1:n);
    endFrames = endFrames(1:n);

    if isfield(stimData, 'stim_trigger_times_hw_spans_multiple_frames') && ~isempty(stimData.stim_trigger_times_hw_spans_multiple_frames)
        spanFlags = logical(stimData.stim_trigger_times_hw_spans_multiple_frames(:));
    elseif isfield(stimData, 'stim_timing') && isstruct(stimData.stim_timing) && ...
            isfield(stimData.stim_timing, 'hardware_spans_multiple_frames') && ~isempty(stimData.stim_timing.hardware_spans_multiple_frames)
        spanFlags = logical(stimData.stim_timing.hardware_spans_multiple_frames(:));
    else
        spanFlags = endFrames > startFrames;
    end
    if numel(spanFlags) < n
        spanFlags(end+1:n,1) = false;
    else
        spanFlags = spanFlags(1:n);
    end
end

function cidx = localTraceCenterIndex(trace, defaultIdx, stimData)
    cidx = defaultIdx;
    if isfield(trace, 'center_index') && ~isempty(trace.center_index) && isfinite(trace.center_index)
        cidx = round(double(trace.center_index));
        return;
    end
    if isfield(stimData, 'centers_um') && isnumeric(stimData.centers_um) && ...
            isfield(trace, 'center_um') && isnumeric(trace.center_um) && numel(trace.center_um) >= 2
        cu = double(stimData.centers_um);
        tv = double(trace.center_um(:).');
        try
            j = find(all(abs(cu(:,1:2) - tv(1:2)) < 1e-6, 2), 1, 'first');
            if ~isempty(j), cidx = j; end
        catch
        end
    end
end

function idx = localTriggerSelectionForCenter(perTrig, centerIdx, nTrig)
    if isempty(perTrig)
        idx = (1:nTrig).';
        return;
    end
    n = min(numel(perTrig), nTrig);
    use = false(n,1);
    for k = 1:n
        e = perTrig(k);
        if isstruct(e) && isfield(e, 'center_index') && ~isempty(e.center_index) && isfinite(e.center_index)
            use(k) = (round(double(e.center_index)) == centerIdx);
        end
    end
    if any(use)
        idx = find(use);
    else
        idx = (1:nTrig).';
    end
end

function segOut = localInterpolateSegmentFrames(segIn, iStart, iEnd)
    segOut = segIn(:).';
    n = numel(segOut);
    if n < 7
        return;
    end
    iStart = max(1, min(n, round(iStart)));
    iEnd = max(1, min(n, round(iEnd)));
    if iEnd < iStart
        t = iStart; iStart = iEnd; iEnd = t;
    end

    % interpolate first affected frame using neighbors [-3 -2 -1 +2 +3]
    fitRel = [-3 -2 -1 2 3];
    src = iStart + fitRel;
    if all(src >= 1 & src <= n) && all(isfinite(segOut(src)))
        p = polyfit(fitRel, segOut(src), 2);
        segOut(iStart) = polyval(p, 0);
    else
        segOut(iStart) = localNeighborInterp(segOut, iStart);
    end

    % if stimulation spans two frames, also interpolate second frame
    if iEnd > iStart
        src2 = (iStart+1) + fitRel;
        if all(src2 >= 1 & src2 <= n) && all(isfinite(segOut(src2)))
            p2 = polyfit(fitRel, segOut(src2), 2);
            segOut(iStart+1) = polyval(p2, 0);
        else
            segOut(iStart+1) = localNeighborInterp(segOut, iStart+1);
        end
    end
end

function v = localNeighborInterp(x, i)
    n = numel(x);
    left = max(1, i-1);
    right = min(n, i+1);
    if left ~= i && right ~= i && isfinite(x(left)) && isfinite(x(right))
        v = 0.5*(x(left) + x(right));
    elseif left ~= i && isfinite(x(left))
        v = x(left);
    elseif right ~= i && isfinite(x(right))
        v = x(right);
    else
        v = x(i);
    end
end

function z = localZscoreSegment(x)
    x = double(x(:).');
    mu = mean(x, 'omitnan');
    sd = std(x, 0, 'omitnan');
    if ~(isfinite(sd) && sd > 0)
        sd = 1;
    end
    z = (x - mu) ./ sd;
    z(~isfinite(z)) = 0;
end

function localPlotFilePsthFigure(figPath, cells, winF, matPath, figVisible)
    n = numel(cells);
    nCol = max(1, ceil(sqrt(n)));
    nRow = ceil(n / nCol);
    h = figure('Visible', localOnOff(figVisible), 'Color', 'w', 'Position', [100 100 1500 900]);
    tl = tiledlayout(h, nRow, nCol, 'Padding', 'compact', 'TileSpacing', 'compact');
    tvec = winF(1):winF(2);

    for i = 1:n
        ax = nexttile(tl);
        mu = cells(i).mean;
        se = cells(i).sem;
        lo = mu - se;
        hi = mu + se;
        fill(ax, [tvec fliplr(tvec)], [lo fliplr(hi)], [0.8 0.9 1.0], 'EdgeColor', 'none'); hold(ax,'on');
        plot(ax, tvec, mu, 'b-', 'LineWidth', 1.3);
        xline(ax, 0, 'r--', 'LineWidth', 1.0);
        xline(ax, 1, 'r:', 'LineWidth', 1.0);
        grid(ax, 'on');
        xlabel(ax, 'Frame rel. stim');
        ylabel(ax, 'z');
        title(ax, sprintf('Cell %d (trace %d, n=%d)', cells(i).center_index, cells(i).trace_index, cells(i).n_trials));
    end
    title(tl, sprintf('PSTH (stimData) | %s', matPath), 'Interpreter', 'none');
    exportgraphics(h, figPath, 'Resolution', 180);
    close(h);
end

function localPlotSingleCellPsthFigure(figPath, cellRes, matPath, figVisible)
    tvec = cellRes.window_frames(:).';
    mu = cellRes.mean(:).';
    se = cellRes.sem(:).';
    lo = mu - se;
    hi = mu + se;

    h = figure('Visible', localOnOff(figVisible), 'Color', 'w', 'Position', [100 100 900 500]);
    ax = axes('Parent', h); %#ok<LAXES>
    fill(ax, [tvec fliplr(tvec)], [lo fliplr(hi)], [0.8 0.9 1.0], 'EdgeColor', 'none'); hold(ax,'on');
    plot(ax, tvec, mu, 'b-', 'LineWidth', 1.5);
    xline(ax, 0, 'r--', 'LineWidth', 1.0);
    xline(ax, 1, 'r:', 'LineWidth', 1.0);
    grid(ax, 'on');
    xlabel(ax, 'Frame rel. stim');
    ylabel(ax, 'z');
    title(ax, sprintf('Cell %d (trace %d, n=%d) | %s', ...
        cellRes.center_index, cellRes.trace_index, cellRes.n_trials, matPath), 'Interpreter', 'none');
    exportgraphics(h, figPath, 'Resolution', 180);
    close(h);
end

function s = localOnOff(tf)
    if tf
        s = 'on';
    else
        s = 'off';
    end
end

function files = localResolveMatFiles(rootDir, matFilesIn)
    files = {};
    if ischar(matFilesIn) || isstring(matFilesIn)
        matFilesIn = cellstr(matFilesIn);
    end
    if ~isempty(matFilesIn)
        for i = 1:numel(matFilesIn)
            p = matFilesIn{i};
            if isstring(p), p = char(p); end
            if ischar(p) && exist(p, 'file') == 2
                files{end+1,1} = p; %#ok<AGROW>
            end
        end
        return;
    end

    stack = {rootDir};
    while ~isempty(stack)
        d = stack{end};
        stack(end) = [];
        L = dir(d);
        for i = 1:numel(L)
            nm = L(i).name;
            if L(i).isdir
                if strcmp(nm,'.') || strcmp(nm,'..'), continue; end
                stack{end+1} = fullfile(d, nm); %#ok<AGROW>
            else
                [~,~,ext] = fileparts(nm);
                if strcmpi(ext, '.mat')
                    files{end+1,1} = fullfile(d, nm); %#ok<AGROW>
                end
            end
        end
    end
end

function fr = localEmptyFileResult()
    fr = struct( ...
        'mat_file', '', ...
        'status', '', ...
        'message', '', ...
        'trigger_source', '', ...
        'n_triggers_total', 0, ...
        'n_cells', 0, ...
        'figure_path', '', ...
        'cells', struct([]));
end

function cr = localEmptyCellResult()
    cr = struct( ...
        'trace_index', NaN, ...
        'center_index', NaN, ...
        'n_trials', 0, ...
        'window_frames', zeros(0,1), ...
        'trials', zeros(0,0), ...
        'mean', zeros(0,1), ...
        'sem', zeros(0,1), ...
        'figure_path', '');
end
