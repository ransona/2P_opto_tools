function [groups, runs, simMatrix, stats] = find_same_red_fov_hardware_runs(rootDir, varargin)
% find_same_red_fov_hardware_runs
% Find stim runs that (1) have hardware trigger timing and (2) share the
% same red FOV image.
%
% Usage:
%   [groups, runs, simMatrix, stats] = find_same_red_fov_hardware_runs(rootDir)
%
% Name-value options:
%   'SimilarityThreshold'        default 0.92
%   'DescriptorSize'             default [64 64]
%   'MinGroupSize'               default 5
%   'RequireExactTriggerCount'   default true
%   'IncludeSingletons'          default false
%   'Verbose'                    default true
%
% Output:
%   groups    struct array with matched run groups
%   runs      struct array with per-run metadata
%   simMatrix NxN cosine similarity between red FOV descriptors
%   stats     summary counts

    if nargin < 1 || isempty(rootDir)
        rootDir = pwd;
    end
    if isstring(rootDir)
        rootDir = char(rootDir);
    end
    validateattributes(rootDir, {'char'}, {'row'});
    assert(exist(rootDir, 'dir') == 7, 'Root folder does not exist: %s', rootDir);

    p = inputParser;
    p.addParameter('SimilarityThreshold', 0.92, @(v)isnumeric(v) && isscalar(v) && isfinite(v) && v > 0 && v <= 1);
    p.addParameter('DescriptorSize', [64 64], @(v)isnumeric(v) && numel(v) == 2 && all(isfinite(v)) && all(v >= 8));
    p.addParameter('MinGroupSize', 5, @(v)isnumeric(v) && isscalar(v) && isfinite(v) && v >= 1);
    p.addParameter('RequireExactTriggerCount', true, @(v)islogical(v) && isscalar(v));
    p.addParameter('IncludeSingletons', false, @(v)islogical(v) && isscalar(v));
    p.addParameter('Verbose', true, @(v)islogical(v) && isscalar(v));
    p.parse(varargin{:});

    similarityThreshold = p.Results.SimilarityThreshold;
    descriptorSize = round(p.Results.DescriptorSize(:).');
    minGroupSize = max(1, round(p.Results.MinGroupSize));
    requireExact = p.Results.RequireExactTriggerCount;
    includeSingletons = p.Results.IncludeSingletons;
    verbose = p.Results.Verbose;

    files = localFindMatFiles(rootDir);
    stats = struct();
    stats.root_dir = rootDir;
    stats.n_mat_files = numel(files);
    stats.n_with_stimData = 0;
    stats.n_hardware_ok = 0;
    stats.n_red_available = 0;
    stats.n_usable = 0;

    runs = struct( ...
        'file', {}, ...
        'session_dir', {}, ...
        'animal_name', {}, ...
        'timestamp', {}, ...
        'red_channel', {}, ...
        'red_image_size', {}, ...
        'n_hw_events', {}, ...
        'n_expected_events', {}, ...
        'hw_source', {}, ...
        'hw_message', {});

    descriptors = zeros(0, prod(descriptorSize), 'double');

    for iFile = 1:numel(files)
        matPath = files{iFile};

        S = [];
        try
            S = load(matPath, 'stimData');
        catch
            continue;
        end
        if ~isfield(S, 'stimData') || ~isstruct(S.stimData) || isempty(S.stimData)
            continue;
        end
        stats.n_with_stimData = stats.n_with_stimData + 1;
        stimData = S.stimData;

        hw = localHardwareInfo(stimData, requireExact);
        if ~hw.ok
            continue;
        end
        stats.n_hardware_ok = stats.n_hardware_ok + 1;

        [imgRed, redChannel] = localGetRedImage(stimData);
        if isempty(imgRed)
            continue;
        end
        stats.n_red_available = stats.n_red_available + 1;

        descriptor = localBuildRedDescriptor(imgRed, descriptorSize);
        if isempty(descriptor)
            continue;
        end

        run = struct();
        run.file = matPath;
        run.session_dir = localGetCharField(stimData, 'session_dir', '');
        run.animal_name = localGetCharField(stimData, 'animal_name', '');
        run.timestamp = localGetCharField(stimData, 'timestamp', '');
        run.red_channel = redChannel;
        run.red_image_size = size(imgRed);
        run.n_hw_events = hw.n_hw;
        run.n_expected_events = hw.n_expected;
        run.hw_source = hw.source;
        run.hw_message = hw.message;

        runs(end+1,1) = run; %#ok<AGROW>
        descriptors(end+1,:) = descriptor(:).'; %#ok<AGROW>
    end

    stats.n_usable = numel(runs);

    if isempty(runs)
        simMatrix = zeros(0, 0);
        groups = localEmptyGroups();
        if verbose
            fprintf('find_same_red_fov_hardware_runs: no usable runs found.\n');
            localPrintStats(stats);
        end
        return;
    end

    simMatrix = descriptors * descriptors.';
    simMatrix = max(-1, min(1, simMatrix));

    adjacency = simMatrix >= similarityThreshold;
    nRuns = size(adjacency, 1);
    adjacency(1:nRuns+1:end) = true;
    components = localConnectedComponents(adjacency);

    groups = localEmptyGroups();
    groupCounter = 0;
    for iComp = 1:numel(components)
        idx = components{iComp};
        if numel(idx) < minGroupSize && ~(includeSingletons && numel(idx) == 1)
            continue;
        end

        subSim = simMatrix(idx, idx);
        pairMask = triu(true(numel(idx), numel(idx)), 1);
        pairVals = subSim(pairMask);
        if isempty(pairVals)
            meanSim = 1;
            minSim = 1;
        else
            meanSim = mean(pairVals, 'omitnan');
            minSim = min(pairVals);
        end

        groupCounter = groupCounter + 1;
        groups(groupCounter,1).group_id = groupCounter; %#ok<AGROW>
        groups(groupCounter,1).member_indices = idx(:).'; %#ok<AGROW>
        groups(groupCounter,1).n_members = numel(idx); %#ok<AGROW>
        groups(groupCounter,1).mean_similarity = meanSim; %#ok<AGROW>
        groups(groupCounter,1).min_similarity = minSim; %#ok<AGROW>
        groups(groupCounter,1).member_files = {runs(idx).file}; %#ok<AGROW>
        groups(groupCounter,1).animal_names = {runs(idx).animal_name}; %#ok<AGROW>
        groups(groupCounter,1).timestamps = {runs(idx).timestamp}; %#ok<AGROW>
    end

    if verbose
        localPrintStats(stats);
        fprintf('Similarity threshold: %.3f\n', similarityThreshold);
        fprintf('Matched groups: %d\n', numel(groups));
        for iGroup = 1:numel(groups)
            g = groups(iGroup);
            fprintf('Group %d: n=%d, mean=%.3f, min=%.3f\n', ...
                g.group_id, g.n_members, g.mean_similarity, g.min_similarity);
            for k = 1:g.n_members
                idx = g.member_indices(k);
                fprintf('  [%d] %s\n', idx, runs(idx).file);
            end
        end
    end
end

function groups = localEmptyGroups()
    groups = struct( ...
        'group_id', {}, ...
        'member_indices', {}, ...
        'n_members', {}, ...
        'mean_similarity', {}, ...
        'min_similarity', {}, ...
        'member_files', {}, ...
        'animal_names', {}, ...
        'timestamps', {});
end

function files = localFindMatFiles(rootDir)
    files = {};
    stack = {rootDir};
    while ~isempty(stack)
        thisDir = stack{end};
        stack(end) = [];
        listing = dir(thisDir);
        for i = 1:numel(listing)
            name = listing(i).name;
            if listing(i).isdir
                if strcmp(name, '.') || strcmp(name, '..')
                    continue;
                end
                stack{end+1} = fullfile(thisDir, name); %#ok<AGROW>
            else
                [~, ~, ext] = fileparts(name);
                if strcmpi(ext, '.mat')
                    files{end+1,1} = fullfile(thisDir, name); %#ok<AGROW>
                end
            end
        end
    end
end

function hw = localHardwareInfo(stimData, requireExact)
    hw = struct('ok', false, 'source', '', 'message', '', 'n_hw', 0, 'n_expected', NaN);

    [timesHw, source] = localGetHardwareTimes(stimData);
    timesHw = timesHw(isfinite(timesHw));
    hw.n_hw = numel(timesHw);
    hw.source = source;

    expected = NaN;
    if isfield(stimData, 'stim_timing') && isstruct(stimData.stim_timing)
        expected = localGetNumericField(stimData.stim_timing, 'expected_event_count', NaN);
        hw.message = localGetCharField(stimData.stim_timing, 'message', '');
    end
    if ~(isfinite(expected) && expected >= 0)
        expected = localGetExpectedTriggerCount(stimData);
    end
    hw.n_expected = expected;

    sourceLabel = localGetCharField(stimData, 'stim_trigger_times_source', '');
    if isempty(sourceLabel) && isfield(stimData, 'stim_timing') && isstruct(stimData.stim_timing)
        sourceLabel = localGetCharField(stimData.stim_timing, 'source', '');
    end
    sourceSaysHardware = contains(lower(sourceLabel), 'hardware');

    hardwareAvailable = false;
    if isfield(stimData, 'stim_timing') && isstruct(stimData.stim_timing) && ...
            isfield(stimData.stim_timing, 'hardware_available')
        hardwareAvailable = localToScalarLogical(stimData.stim_timing.hardware_available);
    end

    countOk = hw.n_hw > 0;
    if requireExact && isfinite(hw.n_expected) && hw.n_expected > 0
        countOk = countOk && (hw.n_hw == hw.n_expected);
    end

    hw.ok = countOk && (hw.n_hw > 0 || hardwareAvailable || sourceSaysHardware);
end

function n = localGetExpectedTriggerCount(stimData)
    n = NaN;
    if isfield(stimData, 'stim_per_trigger') && ~isempty(stimData.stim_per_trigger)
        n = numel(stimData.stim_per_trigger);
        return;
    end
    if isfield(stimData, 'stim_trigger_times_s') && ~isempty(stimData.stim_trigger_times_s)
        v = stimData.stim_trigger_times_s;
        n = numel(v);
        return;
    end
    if isfield(stimData, 'stim_command_times_s') && ~isempty(stimData.stim_command_times_s)
        v = stimData.stim_command_times_s;
        n = numel(v);
    end
end

function [timesHw, source] = localGetHardwareTimes(stimData)
    timesHw = [];
    source = '';
    if isfield(stimData, 'stim_trigger_times_hw_s') && ~isempty(stimData.stim_trigger_times_hw_s)
        timesHw = double(stimData.stim_trigger_times_hw_s(:));
        source = 'stim_trigger_times_hw_s';
        return;
    end
    if isfield(stimData, 'stim_timing') && isstruct(stimData.stim_timing) && ...
            isfield(stimData.stim_timing, 'hardware_times_s') && ~isempty(stimData.stim_timing.hardware_times_s)
        timesHw = double(stimData.stim_timing.hardware_times_s(:));
        source = 'stim_timing.hardware_times_s';
    end
end

function [img, redChannel] = localGetRedImage(stimData)
    img = [];
    redChannel = NaN;
    if ~isfield(stimData, 'cell_picker_red_image') || isempty(stimData.cell_picker_red_image)
        return;
    end

    raw = stimData.cell_picker_red_image;
    if isnumeric(raw) || islogical(raw)
        img = double(raw);
    elseif iscell(raw) && ~isempty(raw) && (isnumeric(raw{1}) || islogical(raw{1}))
        img = double(raw{1});
    else
        return;
    end

    if ndims(img) >= 3
        img = mean(img, 3, 'omitnan');
    end
    if ~ismatrix(img) || isempty(img)
        img = [];
        return;
    end

    finiteMask = isfinite(img);
    if ~any(finiteMask(:))
        img = [];
        return;
    end
    fillVal = median(img(finiteMask));
    img(~finiteMask) = fillVal;

    if isfield(stimData, 'cell_picker_red_channel') && ~isempty(stimData.cell_picker_red_channel)
        ch = double(stimData.cell_picker_red_channel);
        if isfinite(ch(1))
            redChannel = ch(1);
        end
    end
end

function descriptor = localBuildRedDescriptor(img, outSize)
    descriptor = [];
    img = double(img);
    if isempty(img) || ~ismatrix(img)
        return;
    end

    vals = img(isfinite(img));
    if isempty(vals)
        return;
    end

    medVal = median(vals);
    scale = median(abs(vals - medVal));
    if ~(isfinite(scale) && scale > eps)
        scale = std(vals);
    end
    if ~(isfinite(scale) && scale > eps)
        scale = 1;
    end

    img = (img - medVal) ./ scale;
    img(~isfinite(img)) = 0;
    img = localResize2Linear(img, outSize);
    img = img - mean(img(:));

    nrm = norm(img(:));
    if ~(isfinite(nrm) && nrm > eps)
        return;
    end
    descriptor = img(:) ./ nrm;
end

function out = localResize2Linear(img, outSize)
    outH = outSize(1);
    outW = outSize(2);
    [inH, inW] = size(img);

    if inH == outH && inW == outW
        out = img;
        return;
    end

    if inH < 2 || inW < 2
        out = repmat(img(1), outH, outW);
        return;
    end

    x = linspace(1, inW, outW);
    y = linspace(1, inH, outH);
    [X, Y] = meshgrid(x, y);
    out = interp2(1:inW, 1:inH, img, X, Y, 'linear', 0);
end

function components = localConnectedComponents(adjacency)
    n = size(adjacency, 1);
    visited = false(n, 1);
    components = cell(0, 1);

    for i = 1:n
        if visited(i)
            continue;
        end

        queue = i;
        visited(i) = true;
        comp = [];

        while ~isempty(queue)
            v = queue(1);
            queue(1) = [];
            comp(end+1) = v; %#ok<AGROW>

            nbr = find(adjacency(v, :));
            nbr = nbr(~visited(nbr));
            if ~isempty(nbr)
                visited(nbr) = true;
                queue = [queue nbr(:).']; %#ok<AGROW>
            end
        end

        components{end+1,1} = comp; %#ok<AGROW>
    end
end

function v = localGetCharField(s, fieldName, defaultValue)
    v = defaultValue;
    if ~isstruct(s) || ~isfield(s, fieldName)
        return;
    end
    x = s.(fieldName);
    if isstring(x) && isscalar(x)
        v = char(x);
        return;
    end
    if ischar(x)
        v = x;
    end
end

function v = localGetNumericField(s, fieldName, defaultValue)
    v = defaultValue;
    if ~isstruct(s) || ~isfield(s, fieldName)
        return;
    end
    x = s.(fieldName);
    if isempty(x) || ~isnumeric(x)
        return;
    end
    x = x(:);
    x = x(isfinite(x));
    if isempty(x)
        return;
    end
    v = x(1);
end

function tf = localToScalarLogical(v)
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
            tf = localToScalarLogical(char(v));
        else
            tf = any(v ~= "");
        end
    end
end

function localPrintStats(stats)
    fprintf('Root: %s\n', stats.root_dir);
    fprintf('MAT files scanned: %d\n', stats.n_mat_files);
    fprintf('With stimData: %d\n', stats.n_with_stimData);
    fprintf('Hardware timing OK: %d\n', stats.n_hardware_ok);
    fprintf('With red image: %d\n', stats.n_red_available);
    fprintf('Usable runs: %d\n', stats.n_usable);
end
