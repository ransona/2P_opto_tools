global hSI2
global hSICtl2

disp('=======');
disp('Received STOP signal');
hSI2.abort;
startAbort = tic;
while ~strcmp(hSI2.acqState,'idle') && toc(startAbort) < 10
    drawnow();
end

if ~strcmp(hSI2.acqState,'idle')
    error('Timed out waiting for ScanImage to become idle');
end

disp('Stopped');
