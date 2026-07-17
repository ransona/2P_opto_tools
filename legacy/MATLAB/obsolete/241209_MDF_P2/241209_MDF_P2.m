% Most Software Machine Data File

%% scanimage.SI (ScanImage)

% Global microscope properties
objectiveResolution = 73.4096;     % Resolution of the objective in microns/degree of scan angle

% Data file location

% Custom Scripts
startUpScript = '';     % Name of script that is executed in workspace 'base' after scanimage initializes
shutDownScript = '';     % Name of script that is executed in workspace 'base' after scanimage exits

fieldCurvatureZs = [];     % Field curvature for mesoscope
fieldCurvatureRxs = [];     % Field curvature for mesoscope
fieldCurvatureRys = [];     % Field curvature for mesoscope
fieldCurvatureTip = 0;     % Field tip for mesoscope
fieldCurvatureTilt = 0;     % Field tilt for mesoscope
useJsonHeaderFormat = false;     % Use JSON format for TIFF file header

minimizeOnStart = false;
widgetVisibility = true;

%% scanimage.components.CoordinateSystems (SI CoordinateSystems)
% SI Coordinate System Component.
classDataFileName = 'default-CoordinateSystems_classData.mat';     % File containing the previously generated alignment data corresponding to the currently installed objective, SLM, scanners, etc.

%% scanimage.components.Motors (SI Motors)
% SI Stage/Motor Component.
motorXYZ = {'ZaberMotor' 'ZaberMotor' 'ZaberMotor'};     % Defines the motor for ScanImage axes X Y Z.
motorAxisXYZ = [1 2 3];     % Defines the motor axis used for Scanimage axes X Y Z.
scaleXYZ = [1 1 1];     % Defines scaling factors for axes.
backlashCompensation = [0 0 0];     % Backlash compensation in um (positive or negative)
moveTimeout_s = 10;     % Move timeout in seconds

%% scanimage.components.Photostim (SI Photostim)
photostimScannerName = '';     % Name of scanner (from first MDF section) to use for photostimulation. Must be a linear scanner
stimTriggerTerm = 1;     % Specifies the channel that should be used to trigger a stimulation. This a triggering port name such as D2.1 for vDAQ or PFI1 for the auxiliary IO board of an NI LinScan system.

% Monitoring DAQ AI channels
BeamAiId = [];     % AI channel to be used for monitoring the Pockels cell output

loggingStartTrigger = '';     % PFI line to which start trigger for logging is wired to photostim board. Leave empty for automatic routing via PXI bus

stimActiveOutputChannel = '';     % Digital terminal on stim board to output stim active signal. (e.g. on vDAQ: 'D2.6' on NI-DAQ hardware: '/port0/line0'
beamActiveOutputChannel = '';     % Digital terminal on stim board to output beam active signal. (e.g. on vDAQ: 'D2.7' on NI-DAQ hardware: '/port0/line1'
slmTriggerOutputChannel = '';     % Digital terminal on stim board to trigger SLM frame flip. (e.g. on vDAQ: 'D2.5' on NI-DAQ hardware: '/port0/line2'

%% dabs.generic.GalvoPureAnalog (X Galvo P2)
AOControl = '/vDAQ0/AO6';     % control terminal  e.g. '/vDAQ0/AO0'
AOOffset = '';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '/vDAQ0/AI6';     % feedback terminal e.g. '/vDAQ0/AI0'

angularRange = 40;     % total angular range in optical degrees (e.g. for a galvo with -20..+20 optical degrees, enter 40)
voltsPerOpticalDegrees = 0.5;     % volts per optical degrees for the control signal
voltsOffset = 0;     % voltage to be added to the output
parkPosition = 20;     % park position in optical degrees
slewRateLimit = Inf;     % Slew rate limit of the analog output in Volts per second

% Calibration settings
feedbackVoltLUT = zeros(0,2);     % [Nx2] lut translating feedback volts into position volts
offsetVoltScaling = 1;     % scalar factor for offset volts

%% dabs.generic.GalvoPureAnalog (Y Galvo P2)
AOControl = '/vDAQ0/AO7';     % control terminal  e.g. '/vDAQ0/AO0'
AOOffset = '';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '/vDAQ0/AI7';     % feedback terminal e.g. '/vDAQ0/AI0'

angularRange = 40;     % total angular range in optical degrees (e.g. for a galvo with -20..+20 optical degrees, enter 40)
voltsPerOpticalDegrees = 0.5;     % volts per optical degrees for the control signal
voltsOffset = 0;     % voltage to be added to the output
parkPosition = 20;     % park position in optical degrees
slewRateLimit = Inf;     % Slew rate limit of the analog output in Volts per second

% Calibration settings
feedbackVoltLUT = zeros(0,2);     % [Nx2] lut translating feedback volts into position volts
offsetVoltScaling = 1;     % scalar factor for offset volts

%% dabs.generic.BeamModulatorFastAnalog (920nm Power P2)
AOControl = '/vDAQ0/AO8';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '/vDAQ0/AI8';     % feedback terminal e.g. '/vDAQ0/AI0'

outputRange_V = [0 1.5];     % Control output range in Volts
feedbackUsesRejectedLight = false;     % Indicates if photodiode is in rejected path of beams modulator.
calibrationOpenShutters = {'920nm P2'};     % List of shutters to open during the calibration. (e.g. {'Shutter1' 'Shutter2'}

powerFractionLimit = 1;     % Maximum allowed power fraction (between 0 and 1)

% Calibration data
powerFraction2ModulationVoltLut = zeros(0,2);
powerFraction2PowerWattLut = zeros(0,2);
powerFraction2FeedbackVoltLut = zeros(0,2);
feedbackOffset_V = 0;

% Calibration settings
calibrationNumPoints = 100;     % number of equidistant points to measure within the analog output range
calibrationAverageSamples = 5;     % per analog output voltage, average N analog input samples. This helps to reduce noise
calibrationNumRepeats = 5;     % number of times to repeat the calibration routine. the end result is the average of all calibration runs
calibrationSettlingTime_s = 0.001;     % pause between measurement points. this allows the beam modulation to settle
calibrationFlybackTime_s = 0.2;     % pause between calibration runs

% Advanced Settings. Note: these settings are unused for vDAQ based systems
modifiedLineClockIn = '';     % Terminal to which external beam trigger is connected. Leave empty for automatic routing via PXI/RTSI bus
frameClockIn = '';     % Terminal to which external frame clock is connected. Leave empty for automatic routing via PXI/RTSI bus
referenceClockIn = '';     % Terminal to which external reference clock is connected. Leave empty for automatic routing via PXI/RTSI bus
referenceClockRate = 1e+07;     % if referenceClockIn is used, referenceClockRate defines the rate of the reference clock in Hz. Default: 10e6Hz

%% dabs.generic.BeamModulatorFastAnalog (1050nm Power P2)
AOControl = '/vDAQ0/AO10';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '/vDAQ0/AI10';     % feedback terminal e.g. '/vDAQ0/AI0'

outputRange_V = [0 1.5];     % Control output range in Volts
feedbackUsesRejectedLight = false;     % Indicates if photodiode is in rejected path of beams modulator.
calibrationOpenShutters = {'1050nm P2'};     % List of shutters to open during the calibration. (e.g. {'Shutter1' 'Shutter2'}

powerFractionLimit = 1;     % Maximum allowed power fraction (between 0 and 1)

% Calibration data
powerFraction2ModulationVoltLut = zeros(0,2);
powerFraction2PowerWattLut = zeros(0,2);
powerFraction2FeedbackVoltLut = zeros(0,2);
feedbackOffset_V = 0;

% Calibration settings
calibrationNumPoints = 100;     % number of equidistant points to measure within the analog output range
calibrationAverageSamples = 5;     % per analog output voltage, average N analog input samples. This helps to reduce noise
calibrationNumRepeats = 5;     % number of times to repeat the calibration routine. the end result is the average of all calibration runs
calibrationSettlingTime_s = 0.001;     % pause between measurement points. this allows the beam modulation to settle
calibrationFlybackTime_s = 0.2;     % pause between calibration runs

% Advanced Settings. Note: these settings are unused for vDAQ based systems
modifiedLineClockIn = '';     % Terminal to which external beam trigger is connected. Leave empty for automatic routing via PXI/RTSI bus
frameClockIn = '';     % Terminal to which external frame clock is connected. Leave empty for automatic routing via PXI/RTSI bus
referenceClockIn = '';     % Terminal to which external reference clock is connected. Leave empty for automatic routing via PXI/RTSI bus
referenceClockRate = 1e+07;     % if referenceClockIn is used, referenceClockRate defines the rate of the reference clock in Hz. Default: 10e6Hz

%% dabs.generic.ResonantScannerAnalog (X Reso P2)
AOZoom = '/Dev1/AO1';     % zoom control terminal  e.g. '/vDAQ0/AO0'
DOEnable = '/vDAQ0/D1.1';     % digital enable terminal e.g. '/vDAQ0/D0.1'
DISync = '/vDAQ0/D1.0';     % digital sync terminal e.g. '/vDAQ0/D0.0'

nominalFrequency = 7910;     % nominal resonant frequency in Hz
angularRange = 26;     % total angular range in optical degrees (e.g. for a resonant scanner with -13..+13 optical degrees, enter 26)
voltsPerOpticalDegrees = 0.1923;     % volts per optical degrees for the control signal
settleTime = 0.5;     % settle time in seconds to allow the resonant scanner to turn on

% Calibration Settings
amplitudeToLinePhaseMap = zeros(0,2);     % translates an amplitude (degrees) to a line phase (seconds)
amplitudeToFrequencyMap = [26 7918.63];     % translates an amplitude (degrees) to a resonant frequency (Hz)
amplitudeLUT = zeros(0,2);     % translates a nominal amplitude (degrees) to an output amplitude (degrees)

%% dabs.generic.DigitalShutter (920nm P2)
DOControl = '/vDAQ0/D1.2';     % control terminal  e.g. '/vDAQ0/DIO0'
invertOutput = false;     % invert output drive signal to shutter
openTime_s = 0.5;     % settling time for shutter in seconds
shutterTarget = 'Excitation';     % one of {', 'Excitation', 'Detection'}

%% dabs.generic.DigitalShutter (1050nm P2)
DOControl = '/vDAQ0/D1.3';     % control terminal  e.g. '/vDAQ0/DIO0'
invertOutput = false;     % invert output drive signal to shutter
openTime_s = 0.5;     % settling time for shutter in seconds
shutterTarget = 'Excitation';     % one of {', 'Excitation', 'Detection'}

%% scanimage.components.scan2d.RggScan (RGG_P2)

acquisitionDeviceId = 'vDAQ0';     % RDI Device ID
acquisitionEngineIdx = 2;

resonantScanner = 'X Reso P2';     % Name of the resonant scanner
xGalvo = 'X Galvo P2';     % Name of the x galvo scanner
yGalvo = 'Y Galvo P2';     % Name of the y galvo scanner
beams = {'920nm Power P2' '1050nm Power P2'};     % beam device names
fastZs = {};     % fastZ device names
shutters = {'920nm P2' '1050nm P2'};     % shutter device names

channelsInvert = [false false];     % Logical: Specifies if the input signal is inverted (i.e., more negative for increased light signal)
keepResonantScannerOn = false;     % Always keep resonant scanner on to avoid drift and settling time issues
desiredSampleRateCtl = 200000;     % Sampling rate to use for galvo, beams, and fastZ control

externalSampleClock = true;     % Logical: use external sample clock connected to the CLK IN terminal of the FlexRIO digitizer module
externalSampleClockRate = 7.89e+07;     % [Hz]: nominal frequency of the external sample clock connected to the CLK IN terminal (e.g. 80e6); actual rate is measured on FPGA
externalSampleClockMultiplier = 32;     % Multiplier to apply to external sample clock
useCustomFilterClock = false;
customFilterClockPeriod = 32;
sampleClockPhase = [];     % Phase delay to apply to sample clock

extendedRggFov = 0;     % If true and x galvo is present, addressable FOV is combination of resonant FOV and x galvo FOV.

% Advanced/Optional
PeriodClockDebounceTime = 1e-07;     % [s] time the period clock has to be stable before a change is registered
TriggerDebounceTime = 5e-07;     % [s] time acquisition, stop and next trigger to be stable before a change is registered
reverseLineRead = 0;     % flips the image in the resonant scan axis
defaultFlybackTimePerFrame = 0.001;     % [s] default time to allow galvos to fly back after one frame is complete. overridden by cfg file
defaultFlytoTimePerScanfield = 0.001;     % [s] time to allow galvos to fly from one scanfield to the next. overridden by cfg file

% Aux Trigger Recording, Photon Counting, and I2C are mutually exclusive

% Aux Trigger Recording
auxTriggersTimeDebounce = 1e-07;     % [s] time after an edge where subsequent edges are ignored
auxTriggerLinesInvert = [false false false false];     % [logical] 1x4 vector specifying polarity of aux trigger inputs
auxTrigger1In = '';     % Digital input lines for aux trigger 1
auxTrigger2In = '';     % Digital input lines for aux trigger 2
auxTrigger3In = '';     % Digital input lines for aux trigger 3
auxTrigger4In = '';     % Digital input lines for aux trigger 4

% Signal Conditioning
disableMaskDivide = [false false false false];     % disable averaging of samples into pixels; instead accumulate samples
photonDiscriminatorThresholds = [500 500];
photonDiscriminatorModes = {'threshold crossing' 'threshold crossing'};
photonDiscriminatorDifferentiateWidths = [4 4];
enableFpgaPixelCorrection = 0;     % Enable constant coefficient pixel multiplication on the vDAQ FPGA
fpgaPixelCorrectionMultiplier = 'MAXBIN';     % Numeric or string, e.g. 500, 'MAXBIN', 'MINBIN', 'MEANBIN'
enableHostPixelCorrection = false;     % Enable constant coefficient pixel multiplication on the host PC
hostPixelCorrectionMultiplier = 500;     % Numeric or string, e.g. 500, 'MAXBIN', 'MINBIN', 'MEANBIN'

% I2C
i2cEnable = false;
i2cSdaPort = '';
i2cSclPort = '';
i2cAddress = 0;     % [byte] I2C address of the FPGA
i2cDebounce = 1e-07;     % [s] time the I2C signal has to be stable high before a change is registered
i2cStoreAsChar = false;     % if false, the I2C packet bytes are stored as a uint8 array. if true, the I2C packet bytes are stored as a string. Note: a Null byte in the packet terminates the string
i2cSendAck = true;     % When enabled FPGA confirms each packet with an ACK bit by actively pulling down the SDA line

% Laser Trigger
LaserTriggerPort = '/vDAQ0/CLK_IN';     % Digital input where laser trigger is connected.
LRRLaserSyncPort = '';     % Digital input where laser trigger is daisy chained (when plugged into CLK_IN) when using lower repetition rate lasers.

% Trigger Outputs
frameClockOut = '/vDAQ0/D3.3';     % Output line for the frame clock
lineClockOut = '/vDAQ0/D3.5';     % Output line for the line clock
beamModifiedLineClockOut = '';     % Output line for beam clock
volumeTriggerOut = '';     % Output line for the volume clock

% Calibration data
scannerToRefTransform = [1 0 0;0 1 0;0 0 1];
LaserTriggerDebounceTicks = 1;
virtualChannelsSource = {'AI0' 'AI1' 'AI0' 'AI1'};
virtualChannelsMode = {'analog' 'analog' 'analog' 'analog'};
virtualChannelsThreshold = [false false false false];
virtualChannelsBinarize = [false false false false];
virtualChannelsEdgeDetect = [false false false false];
virtualChannelsLaserGate = [false false false false];
virtualChannelsDisableDivide = [false false false false];
virtualChannelsThresholdValue = [100 100 100 100];
virtualChannelsLaserFilterWindow = {[0 1] [0 1] [0 1] [0 1]};

%% dabs.generic.PMTAnalog (Green PMT)
AOGain = '/Dev2/AO0';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/AO0)
AOOutputRange = [0 0.9];     % <required if AOGain is defined> array of 1x2 numeric array specifying the minimum and maximum analog output voltage on the DAQ board that controls the PMT gain.
SupplyVoltageRange = [0 0.9];     % <required if AOGain is defined> array of 1x2 specifying the minimum and maximum for the PMT power supply in Volts.

DOPower = '';     % <optional> resource name of the digital output channel that switches the PMT on/off (e.g. /vDAQ0/D0.0)
DITripDetect = '';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/D0.1)
DOTripReset = '';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/D0.2)

% Calibration settings
wavelength_nm = 510;     % wavelength in nanometer
autoOn = false;     % powers the PMT automatically on for the duration of a scan
gain_V = 0.6;     % PMT power supply voltage

%% dabs.generic.PMTAnalog (Red PMT)
AOGain = '/Dev2/AO1';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/AO0)
AOOutputRange = [0 0.9];     % <required if AOGain is defined> array of 1x2 numeric array specifying the minimum and maximum analog output voltage on the DAQ board that controls the PMT gain.
SupplyVoltageRange = [0 0.9];     % <required if AOGain is defined> array of 1x2 specifying the minimum and maximum for the PMT power supply in Volts.

DOPower = '';     % <optional> resource name of the digital output channel that switches the PMT on/off (e.g. /vDAQ0/D0.0)
DITripDetect = '';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/D0.1)
DOTripReset = '';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/D0.2)

% Calibration settings
wavelength_nm = 630;     % wavelength in nanometer
autoOn = false;     % powers the PMT automatically on for the duration of a scan
gain_V = 0.6;     % PMT power supply voltage

%% dabs.generic.recolorGuis (Recolor Path 2)
scanners = {'RGG_P2'};     % Order of imaging scanners associated with the order of colors. Ordinarily, this should not be modified by the user.
colors = {[0 1 1]};     % Cell array or RGB vectors. Cell ordering corresponds to scanner order above.

%% dabs.resources.daqs.vDAQ_Config (P2 Passive vDAQ Initialization)
vdaqNumber = 0;     % ID number of vDAQ board this configuration should apply to
serialNumber = 'DE3DC902';     % Serial number of vDAQ board
bitfileName = 'vDAQR1_SI.dbs';     % Custom bitfile to load
passiveMode = 1;     % Prevent re-initializing of vDAQ if there is already a loaded design

