using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32;

internal static class ATCommanderLauncher
{
    private static readonly string Root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);

    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        try
        {
            string interpreter = FindInterpreter();
            if (args.Length == 2 && args[0] == "--self-test")
            {
                // No process launch, GUI, network, settings, or credential reads.
                File.WriteAllText(args[1], "{\"gui_subsystem\":true,\"shell\":false,\"create_no_window\":true,\"window_hidden\":true,\"python_available\":" + (interpreter != null ? "true" : "false") + ",\"runtime_ready\":" + (RuntimeReady() ? "true" : "false") + "}");
                return interpreter != null ? 0 : 2;
            }
            if (!File.Exists(Path.Combine(Root, "app.py")) || !File.Exists(Path.Combine(Root, "bootstrap.pyw")))
                throw new InvalidOperationException("Keep AT Commander.exe inside its extracted AT Commander folder. Extract the complete ZIP before opening it.");
            if (interpreter == null)
                throw new InvalidOperationException("AT Commander needs Python 3.10 or newer on this computer. Install Python from python.org/downloads/windows, then open AT Commander.exe again. No command window is needed.");
            // Ready launches retain bootstrap's hidden stderr handling.
            string script = Path.Combine(Root, "bootstrap.pyw");
            ProcessStartInfo start = HiddenStartInfo(interpreter, Quote(script));
            using (Process process = Process.Start(start))
            {
                if (process == null)
                    throw new InvalidOperationException("Windows could not start AT Commander. Open it again after checking the extracted folder.");
                // A ready app should remain open. Immediate failures get a GUI
                // message rather than an invisible crash or command window.
                if (process.WaitForExit(1200) && process.ExitCode != 0)
                    throw new InvalidOperationException("AT Commander could not finish starting. Open it again to run setup, or review atcommander_crash.log in this folder.");
            }
            return 0;
        }
        catch (Exception)
        {
            // Exception text can contain private paths/process arguments. Show
            // only deliberately authored errors for the two known setup cases.
            string message;
            if (!File.Exists(Path.Combine(Root, "app.py")) || !File.Exists(Path.Combine(Root, "bootstrap.pyw")))
                message = "Keep AT Commander.exe inside its extracted AT Commander folder. Extract the complete ZIP before opening it.";
            else if (FindInterpreter() == null)
                message = "AT Commander needs Python 3.10 or newer. Install Python from python.org/downloads/windows, then open AT Commander.exe again.";
            else
                message = "AT Commander could not finish starting. Review atcommander_crash.log in this folder, or remove the .venv/.atc_ready marker and open AT Commander.exe to run setup again.";
            MessageBox.Show(message, "AT Commander", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    internal static ProcessStartInfo HiddenStartInfo(string executable, string arguments)
    {
        return new ProcessStartInfo
        {
            FileName = executable,
            Arguments = arguments,
            WorkingDirectory = Root,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        };
    }

    private static bool RuntimeReady()
    {
        string marker = Path.Combine(Root, ".venv", ".atc_ready");
        string requirements = Path.Combine(Root, "requirements.txt");
        if (!File.Exists(Path.Combine(Root, ".venv", "Scripts", "pythonw.exe")) || !File.Exists(marker) || !File.Exists(requirements))
            return false;
        using (SHA256 digest = SHA256.Create())
        using (FileStream source = File.OpenRead(requirements))
        {
            string expected = BitConverter.ToString(digest.ComputeHash(source)).Replace("-", "");
            return String.Equals(File.ReadAllText(marker).Trim(), expected, StringComparison.OrdinalIgnoreCase);
        }
    }

    private static string FindInterpreter()
    {
        List<string> candidates = new List<string>();
        candidates.Add(Path.Combine(Root, ".venv", "Scripts", "pythonw.exe"));
        candidates.Add(Path.Combine(Root, ".venv", "Scripts", "python.exe"));
        AddRegistryCandidates(candidates, Registry.CurrentUser);
        AddRegistryCandidates(candidates, Registry.LocalMachine);
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        AddPythonDirectories(candidates, Path.Combine(local, "Python"));
        AddPythonDirectories(candidates, Path.Combine(local, "Programs", "Python"));
        foreach (string directory in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
        {
            // Microsoft Store execution aliases may open a storefront instead
            // of Python; use actual installed executables for this launcher.
            if (String.IsNullOrWhiteSpace(directory) || directory.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) >= 0)
                continue;
            candidates.Add(Path.Combine(directory.Trim('"'), "pythonw.exe"));
            candidates.Add(Path.Combine(directory.Trim('"'), "python.exe"));
        }
        foreach (string candidate in candidates.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (!File.Exists(candidate)) continue;
            try
            {
                using (Process probe = Process.Start(HiddenStartInfo(candidate, "-c \"import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 3)\"")))
                {
                    if (probe != null && probe.WaitForExit(4000) && probe.ExitCode == 0)
                        return candidate;
                    if (probe != null && !probe.HasExited) probe.Kill();
                }
            }
            catch (Exception) { }
        }
        return null;
    }

    private static void AddPythonDirectories(List<string> candidates, string directory)
    {
        if (!Directory.Exists(directory)) return;
        try
        {
            candidates.Add(Path.Combine(directory, "pythonw.exe"));
            candidates.Add(Path.Combine(directory, "python.exe"));
            foreach (string child in Directory.GetDirectories(directory).OrderByDescending(value => value))
            {
                candidates.Add(Path.Combine(child, "pythonw.exe"));
                candidates.Add(Path.Combine(child, "python.exe"));
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private static void AddRegistryCandidates(List<string> candidates, RegistryKey hive)
    {
        foreach (string prefix in new[] { @"SOFTWARE\Python", @"SOFTWARE\WOW6432Node\Python" })
        {
            try
            {
                using (RegistryKey python = hive.OpenSubKey(prefix))
                {
                    if (python == null) continue;
                    foreach (string company in python.GetSubKeyNames())
                    using (RegistryKey companyKey = python.OpenSubKey(company))
                    {
                        if (companyKey == null) continue;
                        foreach (string tag in companyKey.GetSubKeyNames().OrderByDescending(value => value))
                        using (RegistryKey install = companyKey.OpenSubKey(tag + @"\InstallPath"))
                        {
                            if (install == null) continue;
                            foreach (string name in new[] { "WindowedExecutablePath", "ExecutablePath" })
                            {
                                string path = install.GetValue(name) as string;
                                if (!String.IsNullOrWhiteSpace(path)) candidates.Add(path);
                            }
                            string home = install.GetValue(null) as string;
                            if (!String.IsNullOrWhiteSpace(home))
                            {
                                candidates.Add(Path.Combine(home, "pythonw.exe"));
                                candidates.Add(Path.Combine(home, "python.exe"));
                            }
                        }
                    }
                }
            }
            catch (System.Security.SecurityException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    private static string Quote(string argument)
    {
        // Windows executable argument quoting; there is no command shell.
        StringBuilder result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char value in argument)
        {
            if (value == '\\') { slashes++; continue; }
            if (value == '"') result.Append('\\', slashes * 2 + 1);
            else result.Append('\\', slashes);
            result.Append(value);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }
}
