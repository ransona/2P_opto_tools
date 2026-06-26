global hSI
global hSICtl

disp('=======');
disp('Received STOP signal');
hSI.abort;
startAbort = tic;
while ~strcmp(hSI.acqState,'idle') && toc(startAbort) < 10
    drawnow();
end

if ~strcmp(hSI.acqState,'idle')
    error('Timed out waiting for ScanImage to become idle');
end

disp('Stopped');
