function importedPatternNames = importSchemaPatterns(hSI, schemaPath, opts)
arguments
    hSI
    schemaPath (1,1) string
    opts.PatternNames string = strings(0, 1)
    opts.ClearExisting (1,1) logical = false
    opts.PythonExecutable (1,1) string = "python"
    opts.StimulusFunction (1,1) string = "point"
    opts.PointSizeXY (1,2) double = [0 0]
    opts.RotationDegrees (1,1) double = 0
    opts.PauseDuration (1,1) double = 0.010
    opts.ParkDuration (1,1) double = 0.010
    opts.XYTransform function_handle = @(xyz)[xyz(1) xyz(2)]
    opts.ZTransform function_handle = @(xyz)xyz(3)
    opts.PowerScaleMode (1,1) string = "multiply"
    opts.IgnoreFrequency (1,1) logical = true
end

if ~isprop(hSI, 'hPhotostim') || isempty(hSI.hPhotostim)
    error('The provided hSI handle does not expose hPhotostim.');
end

schema = opto.scanimage.loadSchemaYaml(schemaPath, opts.PythonExecutable);

if ~isfield(schema, 'patterns')
    error('Schema file does not contain a patterns block: %s', schemaPath);
end

patternNames = getNamedStructKeys(schema.patterns);
if ~isempty(opts.PatternNames)
    patternNames = string(opts.PatternNames(:));
end

if opts.ClearExisting
    hSI.hPhotostim.stimRoiGroups = scanimage.mroi.RoiGroup.empty(1, 0);
end

importedPatternNames = strings(0, 1);
for idx = 1:numel(patternNames)
    patternName = patternNames(idx);
    pattern = getStructByOriginalName(schema.patterns, patternName, schemaPath, "Pattern");
    if ~isfield(pattern, 'name') || strlength(string(pattern.name)) == 0
        pattern.name = patternName;
    end

    hGroup = opto.scanimage.buildStimRoiGroupFromPattern(pattern, ...
        StimulusFunction=opts.StimulusFunction, ...
        PointSizeXY=opts.PointSizeXY, ...
        RotationDegrees=opts.RotationDegrees, ...
        PauseDuration=opts.PauseDuration, ...
        ParkDuration=opts.ParkDuration, ...
        XYTransform=opts.XYTransform, ...
        ZTransform=opts.ZTransform, ...
        PowerScaleMode=opts.PowerScaleMode, ...
        IgnoreFrequency=opts.IgnoreFrequency);

    hSI.hPhotostim.stimRoiGroups(end + 1) = hGroup;
    importedPatternNames(end + 1, 1) = patternName; %#ok<AGROW>
end
end

function names = getNamedStructKeys(s)
fields = string(fieldnames(s));
names = strings(numel(fields), 1);
for i = 1:numel(fields)
    value = s.(fields(i));
    if isstruct(value) && isfield(value, 'name') && strlength(string(value.name)) > 0
        names(i) = string(value.name);
    else
        names(i) = fields(i);
    end
end
end

function value = getStructByOriginalName(s, originalName, context, label)
fieldName = string(matlab.lang.makeValidName(char(string(originalName))));
if ~isfield(s, char(fieldName))
    error('%s "%s" was requested but is not present in schema %s.', label, string(originalName), context);
end
value = s.(char(fieldName));
end
