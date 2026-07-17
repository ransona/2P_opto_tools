function wvfmBuf = BinaryVectorClock2kHz(sampleRate, wvfmParams)
% BinaryVectorClock2kHz
% Reads binaryClock2kHz from base workspace.
% Each element is one sample at 2 kHz (0.5 ms/bin).
% Outputs a digital waveform that is HIGH whenever the vector is nonzero.

    srcRate = 2000; % Hz

    lowVal = 0;
    highVal = 1;
    if isprop(wvfmParams,'restVal_Volts') && isfinite(wvfmParams.restVal_Volts)
        lowVal = double(wvfmParams.restVal_Volts > 0);
    end
    if isprop(wvfmParams,'amplitude_Volts') && isfinite(wvfmParams.amplitude_Volts)
        highVal = double(wvfmParams.amplitude_Volts > 0);
    end

    assert(isfinite(sampleRate) && sampleRate > 0, 'sampleRate must be positive.');
    samplesPerBin = sampleRate / srcRate;
    assert(abs(samplesPerBin - round(samplesPerBin)) < 1e-9, ...
        'sampleRate must be an integer multiple of 2 kHz.');
    samplesPerBin = round(samplesPerBin);

    v = evalin('base','binaryClock2kHz');
    assert(isnumeric(v) || islogical(v), 'binaryClock2kHz must be numeric or logical.');
    v = v(:) ~= 0;

    if isempty(v)
        wvfmBuf = lowVal;
        return;
    end

    wvfmBuf = repelem(v, samplesPerBin);
    wvfmBuf = double(wvfmBuf);
    wvfmBuf(wvfmBuf == 0) = lowVal;
    wvfmBuf(wvfmBuf ~= lowVal) = highVal;

    if isprop(wvfmParams,'startDelay_Sec') && isfinite(wvfmParams.startDelay_Sec) && wvfmParams.startDelay_Sec > 0
        nDelay = round(sampleRate * wvfmParams.startDelay_Sec);
        if nDelay > 0
            wvfmBuf = [repmat(lowVal, nDelay, 1); wvfmBuf(:)];
            return;
        end
    end

    wvfmBuf = wvfmBuf(:);
end
