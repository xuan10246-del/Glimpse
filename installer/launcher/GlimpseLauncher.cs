using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Glimpse 瞬译")]
[assembly: AssemblyDescription("Offline-first desktop real-time screen & audio translator")]
[assembly: AssemblyCompany("Glimpse Open Source")]
[assembly: AssemblyProduct("Glimpse")]
[assembly: AssemblyVersion("3.1.0.0")]
[assembly: AssemblyFileVersion("3.1.0.0")]

namespace GlimpseLauncher
{
    internal static class Program
    {
        private static string QuoteArg(string arg)
        {
            if (string.IsNullOrEmpty(arg))
            {
                return "\"\"";
            }
            if (arg.IndexOfAny(new char[] { ' ', '\t', '"' }) < 0)
            {
                return arg;
            }
            return "\"" + arg.Replace("\"", "\\\"") + "\"";
        }

        private static string BuildArgsString(string prefixArgs, string mainPy, string[] extraArgs)
        {
            StringBuilder sb = new StringBuilder();
            if (!string.IsNullOrEmpty(prefixArgs))
            {
                sb.Append(prefixArgs);
                sb.Append(" ");
            }
            sb.Append(QuoteArg(mainPy));
            if (extraArgs != null)
            {
                for (int i = 0; i < extraArgs.Length; i++)
                {
                    sb.Append(" ");
                    sb.Append(QuoteArg(extraArgs[i]));
                }
            }
            return sb.ToString();
        }

        private static string FindPortablePython(string baseDir, string exeName, out bool needsVenvSitePackages)
        {
            needsVenvSitePackages = false;
            string pythonRoot = Path.Combine(baseDir, "tools\\python");
            string venvSite = Path.Combine(baseDir, ".venv\\Lib\\site-packages");
            if (Directory.Exists(pythonRoot) && Directory.Exists(venvSite))
            {
                try
                {
                    string[] dirs = Directory.GetDirectories(pythonRoot, "cpython-3.12*");
                    Array.Sort(dirs);
                    for (int i = 0; i < dirs.Length; i++)
                    {
                        string candidate = Path.Combine(dirs[i], exeName);
                        if (File.Exists(candidate))
                        {
                            needsVenvSitePackages = true;
                            return candidate;
                        }
                    }
                }
                catch
                {
                }
            }

            string venvExe = Path.Combine(baseDir, ".venv\\Scripts\\" + exeName);
            if (File.Exists(venvExe))
            {
                return venvExe;
            }
            return null;
        }

        [STAThread]
        private static int Main(string[] args)
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
            string toolsDir = Path.Combine(baseDir, "tools");
            string tmpDir = Path.Combine(toolsDir, "tmp");
            string hfDir = Path.Combine(toolsDir, "hf_cache");
            string uvCacheDir = Path.Combine(toolsDir, "uv-cache");
            string uvPyDir = Path.Combine(toolsDir, "python");

            try
            {
                Directory.CreateDirectory(tmpDir);
                Directory.CreateDirectory(hfDir);
            }
            catch
            {
            }

            string mainPy = Path.Combine(baseDir, "app\\main.py");
            if (!File.Exists(mainPy))
            {
                string missingMsg = "未找到主程序入口文件:\n" + mainPy + "\n\n请重新运行安装包或重新解压完整文件。";
#if DEBUG_CONSOLE
                Console.WriteLine(missingMsg);
                if (!Console.IsInputRedirected) Console.ReadKey(true);
#else
                MessageBox.Show(missingMsg, "Glimpse 瞬译 v3.1", MessageBoxButtons.OK, MessageBoxIcon.Error);
#endif
                return 1;
            }

#if DEBUG_CONSOLE
            string pyExeName = "python.exe";
            bool isDebug = true;
#else
            string pyExeName = "pythonw.exe";
            bool isDebug = false;
#endif
            bool needsVenvSitePackages = false;
            string pythonExe = FindPortablePython(baseDir, pyExeName, out needsVenvSitePackages);

            if (string.IsNullOrEmpty(pythonExe))
            {
                string setupBat = Path.Combine(baseDir, "一键安装运行.bat");
                if (File.Exists(setupBat))
                {
                    if (!isDebug)
                    {
                        DialogResult dr = MessageBox.Show(
                            "尚未检测到本地 Python 运行环境或 AI 模型。\n\n点击「确定」将自动打开初始化向导（全部下载与缓存均保存在当前目录，不会写入 C 盘）。",
                            "Glimpse 瞬译 v3.1 - 首次运行提示",
                            MessageBoxButtons.OKCancel,
                            MessageBoxIcon.Information
                        );
                        if (dr != DialogResult.OK)
                        {
                            return 0;
                        }
                    }
                    else
                    {
                        Console.WriteLine("未检测到运行环境，正在启动一键初始化向导...");
                    }

                    ProcessStartInfo setupPsi = new ProcessStartInfo();
                    setupPsi.FileName = "cmd.exe";
                    setupPsi.Arguments = "/c \"\"" + setupBat + "\"\"";
                    setupPsi.WorkingDirectory = baseDir;
                    setupPsi.UseShellExecute = true;
                    Process.Start(setupPsi);
                    return 0;
                }

                string errMsg = "未检测到本地 Python 运行环境 (.venv)，且未找到「一键安装运行.bat」。\n请重新运行 Glimpse Setup 安装程序。";
#if DEBUG_CONSOLE
                Console.WriteLine(errMsg);
                if (!Console.IsInputRedirected) Console.ReadKey(true);
#else
                MessageBox.Show(errMsg, "Glimpse 瞬译 v3.1", MessageBoxButtons.OK, MessageBoxIcon.Error);
#endif
                return 1;
            }

            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = pythonExe;
            psi.WorkingDirectory = baseDir;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = !isDebug;
            psi.Arguments = BuildArgsString(isDebug ? "-u" : "", mainPy, args);

            // Lock all runtime temp and cache env vars inside install folder
            psi.EnvironmentVariables["FOR_DISABLE_CONSOLE_CTRL_HANDLER"] = "1";
            psi.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            psi.EnvironmentVariables["TMP"] = tmpDir;
            psi.EnvironmentVariables["TEMP"] = tmpDir;
            psi.EnvironmentVariables["HF_HOME"] = hfDir;
            psi.EnvironmentVariables["UV_CACHE_DIR"] = uvCacheDir;
            psi.EnvironmentVariables["UV_PYTHON_INSTALL_DIR"] = uvPyDir;

            string existingPath = Environment.GetEnvironmentVariable("PATH") ?? "";
            string venvScripts = Path.Combine(baseDir, ".venv\\Scripts");
            psi.EnvironmentVariables["PATH"] = venvScripts + ";" + toolsDir + ";" + existingPath;

            if (needsVenvSitePackages)
            {
                string venvSite = Path.Combine(baseDir, ".venv\\Lib\\site-packages");
                psi.EnvironmentVariables["PYTHONPATH"] = venvSite + ";" + baseDir;
            }

            try
            {
                Process proc = Process.Start(psi);
                if (proc == null)
                {
                    return 1;
                }
                if (isDebug || (args != null && args.Length > 0))
                {
                    proc.WaitForExit();
                    if (isDebug && proc.ExitCode != 0 && !Console.IsInputRedirected)
                    {
                        Console.WriteLine("\n[Glimpse] 进程异常退出，返回码: " + proc.ExitCode);
                        Console.WriteLine("按任意键关闭窗口...");
                        Console.ReadKey(true);
                    }
                    return proc.ExitCode;
                }
                return 0;
            }
            catch (Exception ex)
            {
                string failMsg = "启动 Glimpse 失败:\n" + ex.Message;
#if DEBUG_CONSOLE
                Console.WriteLine(failMsg);
                if (!Console.IsInputRedirected) Console.ReadKey(true);
#else
                MessageBox.Show(failMsg, "Glimpse 瞬译 v3.1", MessageBoxButtons.OK, MessageBoxIcon.Error);
#endif
                return 1;
            }
        }
    }
}
