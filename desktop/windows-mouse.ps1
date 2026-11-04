param([switch]$DryRun)
$ErrorActionPreference = 'Stop'

# A fixed, local helper. It accepts mouse data only; it never evaluates input as code.
# References: learn.microsoft.com/windows/win32/api/winuser/nf-winuser-sendinput
$source = @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
using System.Web.Script.Serialization;

public static class ManuxMouse {
    [StructLayout(LayoutKind.Sequential)] struct MOUSEINPUT {
        public int dx, dy;
        public uint mouseData, dwFlags, time;
        public UIntPtr dwExtraInfo;
    }
    // MOUSEINPUT is the largest INPUT union member; default alignment preserves
    // the Windows INPUT size (40 bytes on x64, 28 bytes on x86).
    [StructLayout(LayoutKind.Sequential)] struct INPUT { public uint type; public MOUSEINPUT mouse; }
    [DllImport("user32.dll", SetLastError=true)] static extern uint SendInput(uint count, INPUT[] input, int size);
    static readonly object Gate = new object();
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength=8192 };
    static bool Dry, Enabled, Left, Right;
    static long Generation = -1, LastTick;
    static double FractionX, FractionY, FractionWheel;

    static void Output(object value) { Console.Out.WriteLine(Json.Serialize(value)); Console.Out.Flush(); }
    static double UtcMs() { return (DateTime.UtcNow - new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc)).TotalMilliseconds; }
    static bool Fresh(Dictionary<string,object> command) {
        double age = UtcMs() - Number(command, "sentAt", Double.MaxValue);
        return age >= -50 && age <= 300;
    }
    static double Number(Dictionary<string,object> value, string key, double bound) {
        object raw;
        if (!value.TryGetValue(key, out raw) || raw is bool || raw is string || raw == null) throw new Exception("Invalid number.");
        double result = Convert.ToDouble(raw);
        if (Double.IsNaN(result) || Double.IsInfinity(result) || Math.Abs(result) > bound) throw new Exception("Number outside mouse limits.");
        return result;
    }
    static string Text(Dictionary<string,object> value, string key) {
        object raw;
        if (!value.TryGetValue(key, out raw) || !(raw is string)) throw new Exception("Invalid mouse command.");
        return (string)raw;
    }
    static void Keys(Dictionary<string,object> value, params string[] allowed) {
        foreach (string key in value.Keys) if (Array.IndexOf(allowed,key) < 0) throw new Exception("Unexpected command field.");
    }
    static void Validate(Dictionary<string,object> e) {
        if (e == null) throw new Exception("Invalid mouse event.");
        string type = Text(e,"type");
        if (type == "move") { Keys(e,"type","dx","dy"); Number(e,"dx",100); Number(e,"dy",100); }
        else if (type == "scroll") { Keys(e,"type","dy"); Number(e,"dy",240); }
        else if (type == "button") {
            Keys(e,"type","button","down");
            string button = Text(e,"button");
            if ((button != "left" && button != "right") || !e.ContainsKey("down") || !(e["down"] is bool)) throw new Exception("Invalid button.");
        } else if (type == "release") Keys(e,"type");
        else throw new Exception("Unsupported mouse event.");
    }
    static void Emit(uint flags, int dx, int dy, int data) {
        if (Dry) { Output(new {kind="applied", generation=Generation, flags=flags, dx=dx, dy=dy, data=data}); return; }
        INPUT input = new INPUT {type=0, mouse=new MOUSEINPUT {dx=dx, dy=dy, dwFlags=flags, mouseData=unchecked((uint)data)}};
        if (SendInput(1, new INPUT[]{input}, Marshal.SizeOf(typeof(INPUT))) != 1) throw new Exception("Windows rejected mouse input. Elevated apps may block input.");
    }
    static void Release() {
        // Release only buttons pressed by this process, never arbitrary physical buttons.
        if (Left) { try { Emit(0x0004,0,0,0); Left=false; } catch {} }
        if (Right) { try { Emit(0x0010,0,0,0); Right=false; } catch {} }
        FractionX=FractionY=FractionWheel=0;
    }
    static void Stop(string reason) {
        Enabled=false;
        Release();
        Output(new {kind="stopped",generation=Generation,reason=reason});
    }
    static void Event(Dictionary<string,object> e) {
        string type = Text(e,"type");
        if (type == "release") { Release(); return; }
        if (type == "move") {
            FractionX += Number(e,"dx",100); FractionY += Number(e,"dy",100);
            int x=(int)Math.Truncate(FractionX), y=(int)Math.Truncate(FractionY);
            FractionX-=x; FractionY-=y;
            if (x != 0 || y != 0) Emit(0x0001,x,y,0);
        } else if (type == "scroll") {
            // CSS delta is positive downward; Windows WHEEL is positive upward.
            FractionWheel += -Number(e,"dy",240) * 1.2;
            int wheel=(int)Math.Truncate(FractionWheel); FractionWheel-=wheel;
            if (wheel != 0) Emit(0x0800,0,0,wheel);
        } else {
            bool down=(bool)e["down"];
            if (Text(e,"button") == "left" && down != Left) { Emit(down ? 0x0002u : 0x0004u,0,0,0); Left=down; }
            else if (Text(e,"button") == "right" && down != Right) { Emit(down ? 0x0008u : 0x0010u,0,0,0); Right=down; }
        }
    }
    static bool Command(string line) {
        if (line.Length > 8192) throw new Exception("Mouse command is too large.");
        Dictionary<string,object> command=Json.DeserializeObject(line) as Dictionary<string,object>;
        if (command == null) throw new Exception("Invalid command.");
        string kind=Text(command,"kind");
        double rawGeneration=Number(command,"generation",9007199254740991d);
        if (rawGeneration < 0 || rawGeneration != Math.Truncate(rawGeneration)) throw new Exception("Invalid generation.");
        long generation=(long)rawGeneration;
        if (generation < Generation) return true;
        if (kind == "shutdown") { Enabled=false; Release(); return false; }
        if (kind == "disable") { Generation=generation; Enabled=false; Release(); return true; }
        if (kind == "enable") {
            if (generation <= Generation || !Fresh(command)) return true;
            Release(); Generation=generation; Enabled=true; LastTick=Stopwatch.GetTimestamp(); return true;
        }
        if (kind != "batch") throw new Exception("Unknown command.");
        if (!Enabled || generation != Generation) return true;
        if (!Fresh(command)) { Stop("Old input was discarded; enable mouse control again."); return true; }
        object rawEvents;
        if (!command.TryGetValue("events",out rawEvents)) throw new Exception("Missing mouse events.");
        object[] events=rawEvents as object[];
        if (events == null || events.Length > 8) throw new Exception("Invalid mouse batch.");
        foreach (object e in events) Validate(e as Dictionary<string,object>);
        LastTick=Stopwatch.GetTimestamp();
        foreach (object e in events) Event((Dictionary<string,object>)e);
        return true;
    }
    public static void Run(bool dry) {
        Dry=dry;
        Console.OutputEncoding=new System.Text.UTF8Encoding(false);
        using (Timer watchdog=new Timer(delegate(object ignored) {
            lock(Gate) {
                if (Enabled && (Stopwatch.GetTimestamp()-LastTick)*1000d/Stopwatch.Frequency > 900)
                    Stop("Camera input stopped; mouse control was released.");
            }
        }, null, 100, 100)) {
            Output(new {kind="ready",dryRun=Dry,inputSize=Marshal.SizeOf(typeof(INPUT))});
            try {
                string line;
                while ((line=Console.ReadLine()) != null) {
                    lock(Gate) {
                        try { if (!Command(line)) break; }
                        catch(Exception error) { Stop(error.Message); }
                    }
                }
            } finally { lock(Gate) { Enabled=false; Release(); } }
        }
    }
}
'@

Add-Type -TypeDefinition $source -ReferencedAssemblies System.Web.Extensions
[ManuxMouse]::Run([bool]$DryRun)
