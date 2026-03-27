function wvfmBuf = PhotostimTrialClock(sampleRate, wvfmParams)
% PhotostimTrialClock
% Reads photostimTrialTriggerTimesSec, photostimTrialPulseWidthSec, and
% photostimTrialTotalDurationSec from the MATLAB base workspace and
% generates a finite digital waveform with 10 ms-style pulses at the
% requested trigger times.

    assert(isfinite(sampleRate) && sampleRate > 0, 'sampleRate must be positive.');

    lowVal = 0;
    highVal = 1;
    if isprop(wvfmParams, 'restVal_Volts') && isfinite(wvfmParams.restVal_Volts)
        lowVal = double(wvfmParams.restVal_Volts > 0);
    end
    if isprop(wvfmParams, 'amplitude_Volts') && isfinite(wvfmParams.amplitude_Volts)
        highVal = double(wvfmParams.amplitude_Volts > 0);
    end

    triggerTimes = evalin('base', 'photostimTrialTriggerTimesSec');
    pulseWidthSec = evalin('base', 'photostimTrialPulseWidthSec');
    totalDurationSec = evalin('base', 'photostimTrialTotalDurationSec');

    assert(isnumeric(triggerTimes) || islogical(triggerTimes), 'photostimTrialTriggerTimesSec must be numeric.');
    assert(isnumeric(pulseWidthSec) && isscalar(pulseWidthSec) && isfinite(pulseWidthSec) && pulseWidthSec > 0, ...
        'photostimTrialPulseWidthSec must be a positive scalar.');
    assert(isnumeric(totalDurationSec) && isscalar(totalDurationSec) && isfinite(totalDurationSec) && totalDurationSec >= 0, ...
        'photostimTrialTotalDurationSec must be a non-negative scalar.');

    triggerTimes = double(triggerTimes(:));
    if isempty(triggerTimes)
        totalSamples = max(1, round(totalDurationSec * sampleRate));
        wvfmBuf = repmat(lowVal, totalSamples, 1);
        return;
    end

    totalSamples = max(1, ceil(totalDurationSec * sampleRate));
    pulseSamples = max(1, round(pulseWidthSec * sampleRate));
    wvfmBuf = repmat(lowVal, totalSamples, 1);

    for idx = 1:numel(triggerTimes)
        t0 = max(0, triggerTimes(idx));
        startIdx = min(totalSamples, max(1, round(t0 * sampleRate) + 1));
        endIdx = min(totalSamples, startIdx + pulseSamples - 1);
        wvfmBuf(startIdx:endIdx) = highVal;
    end
end
