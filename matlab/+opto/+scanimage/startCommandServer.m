function startCommandServer(rootDir)
if nargin < 1 || isempty(rootDir)
    rootDir = pwd;
end

commandDir = fullfile(rootDir,'.opto_matlab_bridge');
if ~exist(commandDir,'dir')
    mkdir(commandDir);
end

timerName = ['optoBridge_', regexprep(rootDir,'[^A-Za-z0-9]','_')];
existing = timerfindall('Name',timerName);
if ~isempty(existing)
    stop(existing);
    delete(existing);
end

t = timer( ...
    'Name', timerName, ...
    'ExecutionMode', 'fixedSpacing', ...
    'BusyMode', 'drop', ...
    'Period', 0.2, ...
    'TimerFcn', @(~,~)processPendingCommands(commandDir));
start(t);
disp(['Started opto command server in ', commandDir]);
end


function processPendingCommands(commandDir)
files = dir(fullfile(commandDir,'request_*.m'));
for idx = 1:numel(files)
    requestPath = fullfile(files(idx).folder, files(idx).name);
    [~, baseName] = fileparts(requestPath);
    commandId = erase(baseName,'request_');
    processingPath = fullfile(commandDir,['processing_', commandId, '.m']);
    resultPath = fullfile(commandDir,['result_', commandId, '.txt']);

    [moved, moveMsg] = movefile(requestPath, processingPath);
    if ~moved
        if isempty(moveMsg)
            continue;
        end
        writeResult(resultPath, 'STATUS:ERR', {moveMsg});
        continue;
    end

    try
        commandText = fileread(processingPath);
        output = evalc(commandText);
        writeResult(resultPath, 'STATUS:OK', splitlines(output));
    catch ME
        writeResult(resultPath, 'STATUS:ERR', splitlines(getReport(ME,'extended','hyperlinks','off')));
    end

    if exist(processingPath,'file')
        delete(processingPath);
    end
end
end


function writeResult(resultPath, statusLine, lines)
fid = fopen(resultPath,'w');
if fid < 0
    return;
end
cleanup = onCleanup(@() fclose(fid));
fprintf(fid,'%s\n',statusLine);
for idx = 1:numel(lines)
    line = lines{idx};
    if isempty(line)
        fprintf(fid,'\n');
    else
        fprintf(fid,'%s\n',line);
    end
end
end
