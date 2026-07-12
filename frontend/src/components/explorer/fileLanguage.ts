export function fileLanguage(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    // C/C++ (hardware core)
    c: "c",
    cpp: "cpp",
    cc: "cpp",
    cxx: "cpp",
    h: "c",
    hh: "cpp",
    hpp: "cpp",
    ino: "cpp",
    // Python
    py: "python",
    // JS/TS
    js: "javascript",
    ts: "typescript",
    tsx: "typescript",
    jsx: "javascript",
    mjs: "javascript",
    cjs: "javascript",
    // Web
    json: "json",
    md: "markdown",
    markdown: "markdown",
    yaml: "yaml",
    yml: "yaml",
    xml: "xml",
    html: "html",
    htm: "html",
    css: "css",
    scss: "scss",
    sass: "scss",
    less: "less",
    vue: "html",
    svelte: "html",
    // Shell
    sh: "shell",
    bash: "shell",
    zsh: "shell",
    ps1: "powershell",
    bat: "bat",
    cmd: "bat",
    // Systems
    rs: "rust",
    go: "go",
    java: "java",
    kt: "kotlin",
    swift: "swift",
    cs: "csharp",
    rb: "ruby",
    php: "php",
    // Hardware/embedded specific
    pde: "cpp",
    asm: "asm6502",
    s: "asm6502",
    S: "asm6502",
    // Config (hardware common)
    ini: "ini",
    cfg: "ini",
    conf: "ini",
    toml: "ini",
    properties: "ini",
    // Data
    sql: "sql",
    graphql: "graphql",
    proto: "protobuf",
    // Build
    cmake: "cmake",
    makefile: "makefile",
    // Docker
    dockerfile: "dockerfile",
  };
  // 无扩展名的常见文件
  const basename = path.split(/[\\/]/).pop() ?? "";
  if (basename.toLowerCase() === "dockerfile") return "dockerfile";
  if (basename.toLowerCase() === "makefile") return "makefile";
  return map[ext] || "text";
}
