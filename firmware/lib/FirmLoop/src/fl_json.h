#pragma once
// Tiny JSON line writer/reader. No heap, no exceptions. Header-only.
// Writer: appends into a caller-supplied buffer; sets overflow() instead of writing past the end.
// Reader: flat key lookup on a NUL-terminated JSON object; lookups are relative to the object
// whose '{' the pointer addresses (so jsonFind(obj,"given") gives a pointer usable as a new obj).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace fl {

class JsonWriter {
 public:
  JsonWriter(char* buf, size_t cap) : b_(buf), cap_(cap), len_(0), over_(false), comma_(false) {
    if (cap_) b_[0] = 0;
  }
  void reset() { len_ = 0; over_ = false; comma_ = false; if (cap_) b_[0] = 0; }

  void beginObj() { sep(); put('{'); comma_ = false; }
  void endObj() { put('}'); comma_ = true; }
  void beginArr() { sep(); put('['); comma_ = false; }
  void endArr() { put(']'); comma_ = true; }

  void key(const char* k) { sep(); quoted(k); put(':'); comma_ = false; }

  void str(const char* s) { sep(); quoted(s ? s : ""); comma_ = true; }
  void i32(int32_t v) { char t[16]; snprintf(t, sizeof t, "%d", (int)v); sep(); raw(t); comma_ = true; }
  void u32(uint32_t v) { char t[16]; snprintf(t, sizeof t, "%u", (unsigned)v); sep(); raw(t); comma_ = true; }
  void f32(float v, int decimals) {
    char t[32]; snprintf(t, sizeof t, "%.*f", decimals, (double)v); sep(); raw(t); comma_ = true;
  }
  void boolean(bool v) { sep(); raw(v ? "true" : "false"); comma_ = true; }

  // Append pre-formatted text verbatim (caller guarantees validity). Does not touch comma state.
  void raw(const char* s) { while (*s) put(*s++); }

  const char* c_str() const { return b_; }
  size_t size() const { return len_; }
  bool overflow() const { return over_; }

 private:
  void sep() { if (comma_) put(','); comma_ = false; }
  void put(char c) {
    if (len_ + 1 < cap_) { b_[len_++] = c; b_[len_] = 0; } else { over_ = true; }
  }
  void quoted(const char* s) {
    put('"');
    for (; *s; ++s) {
      unsigned char c = (unsigned char)*s;
      if (c == '"' || c == '\\') { put('\\'); put((char)c); }
      else if (c == '\n') { put('\\'); put('n'); }
      else if (c == '\r') { put('\\'); put('r'); }
      else if (c == '\t') { put('\\'); put('t'); }
      else if (c < 0x20) {
        static const char hex[] = "0123456789abcdef";
        put('\\'); put('u'); put('0'); put('0'); put(hex[c >> 4]); put(hex[c & 15]);
      } else put((char)c);
    }
    put('"');
  }
  char* b_;
  size_t cap_, len_;
  bool over_, comma_;
};

namespace jsondetail {
inline const char* skipWs(const char* p) {
  while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') ++p;
  return p;
}
// p at opening quote; returns pointer just after the closing quote (or nullptr if unterminated).
inline const char* skipString(const char* p) {
  ++p;
  while (*p) {
    if (*p == '\\') { if (!p[1]) return nullptr; p += 2; continue; }
    if (*p == '"') return p + 1;
    ++p;
  }
  return nullptr;
}
// p at the start of a value; returns pointer after the value (or nullptr on malformed input).
inline const char* skipValue(const char* p) {
  p = skipWs(p);
  if (*p == '"') return skipString(p);
  if (*p == '{' || *p == '[') {
    int depth = 0;
    while (*p) {
      if (*p == '"') { p = skipString(p); if (!p) return nullptr; continue; }
      if (*p == '{' || *p == '[') ++depth;
      else if (*p == '}' || *p == ']') { if (--depth == 0) return p + 1; }
      ++p;
    }
    return nullptr;
  }
  while (*p && *p != ',' && *p != '}' && *p != ']') ++p;
  return p;
}
}  // namespace jsondetail

// Find the value of `key` directly inside the object at `obj` (which must point at '{').
// Returns a pointer to the first character of the value, or nullptr.
inline const char* jsonFind(const char* obj, const char* key) {
  using namespace jsondetail;
  if (!obj) return nullptr;
  const char* p = skipWs(obj);
  if (*p != '{') return nullptr;
  ++p;
  size_t klen = strlen(key);
  for (;;) {
    p = skipWs(p);
    if (*p == '}' || *p == 0) return nullptr;
    if (*p == ',') { ++p; continue; }
    if (*p != '"') return nullptr;
    const char* ks = p + 1;
    const char* ke = skipString(p);
    if (!ke) return nullptr;
    bool match = ((size_t)(ke - 1 - ks) == klen) && strncmp(ks, key, klen) == 0;
    p = skipWs(ke);
    if (*p != ':') return nullptr;
    p = skipWs(p + 1);
    if (match) return p;
    p = skipValue(p);
    if (!p) return nullptr;
  }
}

inline bool jsonGetFloat(const char* obj, const char* key, float* out) {
  const char* v = jsonFind(obj, key);
  if (!v) return false;
  char* end = nullptr;
  float f = strtof(v, &end);
  if (end == v) return false;
  *out = f;
  return true;
}

inline bool jsonGetInt(const char* obj, const char* key, int32_t* out) {
  const char* v = jsonFind(obj, key);
  if (!v) return false;
  char* end = nullptr;
  long n = strtol(v, &end, 10);
  if (end == v) return false;
  *out = (int32_t)n;
  return true;
}

inline bool jsonGetBool(const char* obj, const char* key, bool* out) {
  const char* v = jsonFind(obj, key);
  if (!v) return false;
  if (strncmp(v, "true", 4) == 0) { *out = true; return true; }
  if (strncmp(v, "false", 5) == 0) { *out = false; return true; }
  return false;
}

// Copies the unescaped string value into out (NUL-terminated). Returns false if the key is
// missing, not a string, or the result does not fit in cap bytes (including the NUL).
inline bool jsonGetString(const char* obj, const char* key, char* out, size_t cap) {
  const char* v = jsonFind(obj, key);
  if (!v || *v != '"' || cap == 0) return false;
  size_t n = 0;
  for (const char* p = v + 1; *p; ++p) {
    char c = *p;
    if (c == '"') { out[n] = 0; return true; }
    if (c == '\\') {
      ++p;
      switch (*p) {
        case 'n': c = '\n'; break;
        case 'r': c = '\r'; break;
        case 't': c = '\t'; break;
        case 0: return false;
        default: c = *p; break;  // \" \\ \/ (\uXXXX is not supported: kept as the letter u)
      }
    }
    if (n + 1 >= cap) return false;
    out[n++] = c;
  }
  return false;
}

}  // namespace fl
