// SPDX-License-Identifier: MIT
// The part of the UnityEngine API that managed code can use in a hybrid unity_pack build (--hybrid): script methods the packer cannot lower to C run
// here, on DotNetAnywhere, from their own C# source.  Pure C#: nothing in this file talks to the packed engine.  What does (a class's fields, its
// transform, Time) is generated beside it by tools/unity_pack_hybrid.py, from the same accessors the lowered C code uses.
//
// A member this shim does not have is a compile error for the class that uses it, and that class keeps its stub (with the old CS8000 warning):
// a hybrid build never changes what a pack that worked did.
using System;

namespace UnityEngine
{
    [AttributeUsage(AttributeTargets.All)] public sealed class SerializeFieldAttribute : Attribute { }
    [AttributeUsage(AttributeTargets.All)] public sealed class HideInInspectorAttribute : Attribute { }
    [AttributeUsage(AttributeTargets.All)] public sealed class HeaderAttribute : Attribute { public HeaderAttribute(string h) { } }
    [AttributeUsage(AttributeTargets.All)] public sealed class TooltipAttribute : Attribute { public TooltipAttribute(string t) { } }
    [AttributeUsage(AttributeTargets.All)] public sealed class RangeAttribute : Attribute { public RangeAttribute(float a, float b) { } }

    public struct Vector2
    {
        public float x, y;
        public Vector2(float x, float y) { this.x = x; this.y = y; }
        public static Vector2 zero { get { return new Vector2(0f, 0f); } }
        public static Vector2 one { get { return new Vector2(1f, 1f); } }
        public static Vector2 up { get { return new Vector2(0f, 1f); } }
        public static Vector2 right { get { return new Vector2(1f, 0f); } }
        public float magnitude { get { return (float)Math.Sqrt(x * x + y * y); } }
        public float sqrMagnitude { get { return x * x + y * y; } }
        public Vector2 normalized { get { float m = magnitude; return m > 1e-5f ? new Vector2(x / m, y / m) : new Vector2(0f, 0f); } }
        public static float Dot(Vector2 a, Vector2 b) { return a.x * b.x + a.y * b.y; }
        public static float Distance(Vector2 a, Vector2 b) { return (a - b).magnitude; }
        public static Vector2 operator +(Vector2 a, Vector2 b) { return new Vector2(a.x + b.x, a.y + b.y); }
        public static Vector2 operator -(Vector2 a, Vector2 b) { return new Vector2(a.x - b.x, a.y - b.y); }
        public static Vector2 operator -(Vector2 a) { return new Vector2(-a.x, -a.y); }
        public static Vector2 operator *(Vector2 a, float k) { return new Vector2(a.x * k, a.y * k); }
        public static Vector2 operator *(float k, Vector2 a) { return new Vector2(a.x * k, a.y * k); }
        public static Vector2 operator /(Vector2 a, float k) { return new Vector2(a.x / k, a.y / k); }
        public static bool operator ==(Vector2 a, Vector2 b) { return a.x == b.x && a.y == b.y; }
        public static bool operator !=(Vector2 a, Vector2 b) { return !(a.x == b.x && a.y == b.y); }
        public override bool Equals(object o) { return o is Vector2 && this == (Vector2)o; }
        public override int GetHashCode() { return x.GetHashCode() ^ (y.GetHashCode() << 1); }
    }

    public struct Vector3
    {
        public float x, y, z;
        public Vector3(float x, float y, float z) { this.x = x; this.y = y; this.z = z; }
        public Vector3(float x, float y) { this.x = x; this.y = y; this.z = 0f; }
        public static Vector3 zero { get { return new Vector3(0f, 0f, 0f); } }
        public static Vector3 one { get { return new Vector3(1f, 1f, 1f); } }
        public static Vector3 up { get { return new Vector3(0f, 1f, 0f); } }
        public static Vector3 right { get { return new Vector3(1f, 0f, 0f); } }
        public static Vector3 forward { get { return new Vector3(0f, 0f, 1f); } }
        public float magnitude { get { return (float)Math.Sqrt(x * x + y * y + z * z); } }
        public float sqrMagnitude { get { return x * x + y * y + z * z; } }
        public Vector3 normalized { get { float m = magnitude; return m > 1e-5f ? new Vector3(x / m, y / m, z / m) : new Vector3(0f, 0f, 0f); } }
        public static float Dot(Vector3 a, Vector3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
        public static float Distance(Vector3 a, Vector3 b) { return (a - b).magnitude; }
        public static Vector3 operator +(Vector3 a, Vector3 b) { return new Vector3(a.x + b.x, a.y + b.y, a.z + b.z); }
        public static Vector3 operator -(Vector3 a, Vector3 b) { return new Vector3(a.x - b.x, a.y - b.y, a.z - b.z); }
        public static Vector3 operator -(Vector3 a) { return new Vector3(-a.x, -a.y, -a.z); }
        public static Vector3 operator *(Vector3 a, float k) { return new Vector3(a.x * k, a.y * k, a.z * k); }
        public static Vector3 operator *(float k, Vector3 a) { return new Vector3(a.x * k, a.y * k, a.z * k); }
        public static Vector3 operator /(Vector3 a, float k) { return new Vector3(a.x / k, a.y / k, a.z / k); }
        public static bool operator ==(Vector3 a, Vector3 b) { return a.x == b.x && a.y == b.y && a.z == b.z; }
        public static bool operator !=(Vector3 a, Vector3 b) { return !(a.x == b.x && a.y == b.y && a.z == b.z); }
        public override bool Equals(object o) { return o is Vector3 && this == (Vector3)o; }
        public override int GetHashCode() { return x.GetHashCode() ^ (y.GetHashCode() << 1) ^ (z.GetHashCode() << 2); }
    }

    public static class Mathf
    {
        public const float PI = 3.14159265358979f;
        public const float Deg2Rad = PI / 180f;
        public const float Rad2Deg = 180f / PI;
        public static float Abs(float f) { return f < 0f ? -f : f; }
        public static int Abs(int i) { return i < 0 ? -i : i; }
        public static float Sqrt(float f) { return (float)Math.Sqrt(f); }
        public static float Sin(float f) { return (float)Math.Sin(f); }
        public static float Cos(float f) { return (float)Math.Cos(f); }
        public static float Atan2(float y, float x) { return (float)Math.Atan2(y, x); }
        public static float Pow(float f, float p) { return (float)Math.Pow(f, p); }
        public static float Min(float a, float b) { return a < b ? a : b; }
        public static float Max(float a, float b) { return a > b ? a : b; }
        public static int Min(int a, int b) { return a < b ? a : b; }
        public static int Max(int a, int b) { return a > b ? a : b; }
        public static float Clamp(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
        public static int Clamp(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }
        public static float Clamp01(float v) { return v < 0f ? 0f : (v > 1f ? 1f : v); }
        public static float Lerp(float a, float b, float t) { return a + (b - a) * Clamp01(t); }
        public static float Floor(float f) { return (float)Math.Floor(f); }
        public static float Ceil(float f) { return (float)Math.Ceiling(f); }
        public static int FloorToInt(float f) { return (int)Math.Floor(f); }
        public static int CeilToInt(float f) { return (int)Math.Ceiling(f); }
        public static int RoundToInt(float f) { return (int)Math.Round(f); }
        public static float Sign(float f) { return f >= 0f ? 1f : -1f; }
    }

    public static class Debug
    {
        public static void Log(object o) { Console.WriteLine(o == null ? "null" : o.ToString()); }
        public static void LogWarning(object o) { Console.WriteLine(o == null ? "null" : o.ToString()); }
        public static void LogError(object o) { Console.WriteLine(o == null ? "null" : o.ToString()); }
    }

    /** The transform of the object a script belongs to.  Which engine arrays it reads is the packer's to say, so each hybrid class has a subclass (generated). */
    public abstract class Transform
    {
        public abstract Vector3 position { get; set; }
        public Vector3 localPosition { get { return position; } set { position = value; } }
    }

    public class MonoBehaviour
    {
        /** the object's index in its class's packed arrays: the `i` of every `Class_method(i, ..)` in engine.c */
        public uint __i;
        public Transform transform;
    }
}
