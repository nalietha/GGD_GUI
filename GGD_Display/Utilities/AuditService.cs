using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

#if USE_JSON_SCHEMA
using Newtonsoft.Json.Schema;
#endif

namespace GGD_Display.Services.Audit
{
    /// <summary>
    /// Describes how to read a "version" from a file.
    /// </summary>
    public enum VersionSource
    {
        None,               // No version comparison
        AssemblyFileVersion, // Windows FileVersionInfo or AssemblyName version (for .dll/.exe)
        JsonPath,           // Read from a JSONPath (e.g. $.Metadata.Version)
        Regex               // Extract from text via Regex
    }

    /// <summary>
    /// Rule describing a required file and how to validate it.
    /// </summary>
    public sealed class FileAuditRule
    {
        public string Path { get; init; } = "";
        public bool MustExist { get; init; } = true;

        // Version checking
        public VersionSource VersionSource { get; init; } = VersionSource.None;
        public string? VersionSelector { get; init; } // JSONPath or Regex pattern depending on VersionSource

        /// <summary>
        /// Name of a "version group" this file participates in (e.g., "AppVersion").
        /// Files in the same group are expected to have the SAME version string.
        /// </summary>
        public string? VersionGroup { get; init; }

        // JSON checks
        public bool IsJson { get; init; } = false;
        public bool AutoFormatJson { get; init; } = false;

#if USE_JSON_SCHEMA
        /// <summary>
        /// Optional path to a JSON schema (*.json) to validate this JSON file against.
        /// </summary>
        public string? JsonSchemaPath { get; init; }
#endif
    }

    /// <summary>
    /// Result for a single file audit.
    /// </summary>
    public sealed class FileAuditResult
    {
        public string Path { get; init; } = "";
        public bool Exists { get; set; }
        public string? Version { get; set; }
        public bool JsonValid { get; set; }
        public bool JsonFormatted { get; set; }
        public List<string> Errors { get; } = new();
        public List<string> Warnings { get; } = new();

        public override string ToString()
        {
            var sb = new StringBuilder();
            sb.AppendLine($"File: {Path}");
            sb.AppendLine($"  Exists: {Exists}");
            if (Version != null) sb.AppendLine($"  Version: {Version}");
            if (JsonValid || JsonFormatted || Errors.Count > 0)
            {
                sb.AppendLine($"  JSON valid: {JsonValid}");
                if (JsonFormatted) sb.AppendLine($"  JSON formatted: {JsonFormatted}");
            }
            foreach (var w in Warnings) sb.AppendLine($"  Warning: {w}");
            foreach (var e in Errors) sb.AppendLine($"  Error: {e}");
            return sb.ToString();
        }
    }

    /// <summary>
    /// Aggregated audit output.
    /// </summary>
    public sealed class AuditReport
    {
        public DateTime RanAtUtc { get; init; } = DateTime.UtcNow;
        public List<FileAuditResult> Files { get; } = new();
        public List<string> CrossChecks { get; } = new(); // e.g., version group comparisons
        public bool Success => Files.All(f => f.Exists && f.Errors.Count == 0);

        public override string ToString()
        {
            var sb = new StringBuilder();
            sb.AppendLine($"Audit ran at (UTC): {RanAtUtc:O}");
            foreach (var f in Files) sb.AppendLine(f.ToString());
            foreach (var c in CrossChecks) sb.AppendLine($"CrossCheck: {c}");
            sb.AppendLine($"Overall success: {Success}");
            return sb.ToString();
        }
    }

    /// <summary>
    /// The main audit service. Configure it with FileAuditRule entries and run Audit().
    /// </summary>
    public sealed class AuditService
    {
        private readonly IReadOnlyList<FileAuditRule> _rules;

        public AuditService(IEnumerable<FileAuditRule> rules)
        {
            _rules = rules?.ToList() ?? throw new ArgumentNullException(nameof(rules));
        }

        public AuditReport Audit()
        {
            var report = new AuditReport();
            var versionGroups = new Dictionary<string, List<(string file, string? version)>>(
                StringComparer.OrdinalIgnoreCase);

            foreach (var rule in _rules)
            {
                var result = new FileAuditResult { Path = rule.Path };
                try
                {
                    result.Exists = File.Exists(rule.Path);
                    if (rule.MustExist && !result.Exists)
                    {
                        result.Errors.Add("File is required but missing.");
                        report.Files.Add(result);
                        continue;
                    }

                    if (!result.Exists)
                    {
                        // Not required and not present → no further checks.
                        report.Files.Add(result);
                        continue;
                    }

                    // Version extraction
                    if (rule.VersionSource != VersionSource.None)
                    {
                        result.Version = ExtractVersion(rule);
                        if (rule.VersionGroup != null)
                        {
                            if (!versionGroups.TryGetValue(rule.VersionGroup, out var list))
                            {
                                list = new List<(string, string?)>();
                                versionGroups[rule.VersionGroup] = list;
                            }
                            list.Add((rule.Path, result.Version));
                        }
                    }

                    // JSON validation
                    if (rule.IsJson)
                    {
                        ValidateAndFormatJson(rule, result);
                    }
                }
                catch (Exception ex)
                {
                    result.Errors.Add($"Unhandled exception: {ex.GetType().Name}: {ex.Message}");
                }
                finally
                {
                    report.Files.Add(result);
                }
            }

            // Cross-validate version groups
            foreach (var kvp in versionGroups)
            {
                var group = kvp.Key;
                var versions = kvp.Value;

                var nonNull = versions.Where(v => !string.IsNullOrWhiteSpace(v.version))
                                      .Select(v => v.version!.Trim())
                                      .Distinct(StringComparer.OrdinalIgnoreCase)
                                      .ToList();

                if (nonNull.Count <= 1)
                {
                    report.CrossChecks.Add($"Version group '{group}' OK ({nonNull.FirstOrDefault() ?? "n/a"}).");
                }
                else
                {
                    var details = string.Join(", ",
                        versions.Select(v => $"{Path.GetFileName(v.file)}={v.version ?? "null"}"));
                    report.CrossChecks.Add($"Version group '{group}' MISMATCH: {details}");
                    // Also mark each file with a warning
                    foreach (var f in report.Files.Where(f => versions.Any(v => v.file.Equals(f.Path, StringComparison.OrdinalIgnoreCase))))
                    {
                        f.Warnings.Add($"Version group '{group}' has mismatched versions across files.");
                    }
                }
            }

            return report;
        }

        private static string? ExtractVersion(FileAuditRule rule)
        {
            switch (rule.VersionSource)
            {
                case VersionSource.AssemblyFileVersion:
                    return ReadAssemblyOrFileVersion(rule.Path);

                case VersionSource.JsonPath:
                    if (string.IsNullOrWhiteSpace(rule.VersionSelector))
                        throw new InvalidOperationException("VersionSelector (JSONPath) required for VersionSource.JsonPath.");

                    using (var sr = File.OpenText(rule.Path))
                    using (var jr = new JsonTextReader(sr))
                    {
                        var token = JToken.ReadFrom(jr);
                        var sel = token.SelectToken(rule.VersionSelector!);
                        return sel?.ToString();
                    }

                case VersionSource.Regex:
                    if (string.IsNullOrWhiteSpace(rule.VersionSelector))
                        throw new InvalidOperationException("VersionSelector (Regex) required for VersionSource.Regex.");
                    var text = File.ReadAllText(rule.Path);
                    var m = Regex.Match(text, rule.VersionSelector!, RegexOptions.Multiline);
                    if (m.Success)
                    {
                        if (m.Groups.Count > 1) return m.Groups[1].Value;
                        return m.Value;
                    }
                    return null;

                default:
                    return null;
            }
        }

        private static string? ReadAssemblyOrFileVersion(string path)
        {
            try
            {
                // Try file version info (works for .exe/.dll on all OS, returns whatever is embedded)
                var fvi = FileVersionInfo.GetVersionInfo(path);
                if (!string.IsNullOrWhiteSpace(fvi.FileVersion))
                    return fvi.FileVersion;

                // As a fallback, try AssemblyName version
                if (Path.GetExtension(path).Equals(".dll", StringComparison.OrdinalIgnoreCase) ||
                    Path.GetExtension(path).Equals(".exe", StringComparison.OrdinalIgnoreCase))
                {
                    var asmName = AssemblyName.GetAssemblyName(path);
                    return asmName?.Version?.ToString();
                }
            }
            catch
            {
                // Swallow and return null; caller will note missing version.
            }
            return null;
        }

        private static void ValidateAndFormatJson(FileAuditRule rule, FileAuditResult result)
        {
            string raw = File.ReadAllText(rule.Path);

            // Try parsing first
            JToken? token = null;
            try
            {
                using var sr = new StringReader(raw);
                using var jr = new JsonTextReader(sr)
                {
                    // You can toggle these depending on your style
                    CloseInput = false
                };
                token = JToken.ReadFrom(jr);
                result.JsonValid = true;
            }
            catch (JsonReaderException jex)
            {
                result.JsonValid = false;
                result.Errors.Add($"Invalid JSON: {jex.Message}");
                return;
            }

#if USE_JSON_SCHEMA
            // Optional: schema validation
            if (!string.IsNullOrWhiteSpace(rule.JsonSchemaPath))
            {
                try
                {
                    var schemaText = File.ReadAllText(rule.JsonSchemaPath!);
                    var schema = JSchema.Parse(schemaText);
                    if (token != null && !token.IsValid(schema, out IList<string> schemaErrors))
                    {
                        foreach (var e in schemaErrors) result.Errors.Add($"Schema: {e}");
                    }
                }
                catch (Exception ex)
                {
                    result.Warnings.Add($"Schema validation skipped: {ex.GetType().Name}: {ex.Message}");
                }
            }
#endif

            if (rule.AutoFormatJson && token != null)
            {
                try
                {
                    var pretty = token.ToString(Formatting.Indented);
                    if (!StringEqualsNormalized(raw, pretty))
                    {
                        File.WriteAllText(rule.Path, pretty + Environment.NewLine, new UTF8Encoding(false));
                        result.JsonFormatted = true;
                    }
                }
                catch (Exception ex)
                {
                    result.Warnings.Add($"Failed to auto-format JSON: {ex.GetType().Name}: {ex.Message}");
                }
            }
        }

        private static bool StringEqualsNormalized(string a, string b)
        {
            // Ignore CRLF vs LF differences and trailing whitespace
            static string Norm(string s) =>
                string.Join("\n", s.Replace("\r\n", "\n").Split('\n').Select(line => line.TrimEnd()));
            return string.Equals(Norm(a), Norm(b), StringComparison.Ordinal);
        }
    }
}
