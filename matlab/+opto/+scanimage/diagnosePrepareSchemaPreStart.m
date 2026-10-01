function report = diagnosePrepareSchemaPreStart(hSI, schemaSource)
% Profile schema photostim preparation without starting mask generation.
% This isolates pre-hPs.start() work and reports the slowest MATLAB calls.
arguments
    hSI
    schemaSource
end

profile clear;
profile on -history;
timer = tic();
try
    [report.importedPatternNames, report.patternNumbers] = ...
        opto.scanimage.prepareSchemaPhotostim( ...
            hSI, ...
            schemaSource, ...
            ConfigureSequence=true, ...
            StartPhotostim=false);
catch ME
    profile off;
    rethrow(ME);
end
profile off;

report.elapsedSeconds = toc(timer);
profileInfo = profile('info');
report.profile = profileInfo;

fprintf('PRESTART_PROFILE total: %.3f s\n', report.elapsedSeconds);
if ~isfield(profileInfo, 'FunctionTable') || isempty(profileInfo.FunctionTable)
    fprintf('PRESTART_PROFILE no function data returned\n');
    return;
end

functionTable = profileInfo.FunctionTable;
totalTimes = [functionTable.TotalTime];
[~, order] = sort(totalTimes, 'descend');
maxRows = min(20, numel(order));
fprintf('PRESTART_PROFILE top functions by total time:\n');
for rowIdx = 1:maxRows
    functionInfo = functionTable(order(rowIdx));
    fprintf('PRESTART_PROFILE %2d total %.3f s calls %d %s\n', ...
        rowIdx, ...
        functionInfo.TotalTime, ...
        functionInfo.NumCalls, ...
        functionInfo.FunctionName);
end
end
