// RGen -- generates Android R.java classes for the javac-based compile check (tools/compilecheck).
//
//   java -cp .cache/tools RGen app  <outDir> <package> --res <resDir> [--res <dir>...] [--string <name>...] [--sym <type>:<name>...]
//   java -cp .cache/tools RGen rtxt <outDir> <package> <R.txt>
//
// "app" scans resource directories the way aapt2 names resources (values XML
// elements, file-based resources by directory type, @+id/@id references) and
// writes <outDir>/<package path>/R.java with arbitrary but unique values.
// "rtxt" turns a library AAR's R.txt into an R.java (used for library R
// classes the app references explicitly, e.g. androidx.appcompat.R).
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import javax.lang.model.SourceVersion;
import javax.xml.parsers.DocumentBuilderFactory;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;

public class RGen {
    // Resource directory types whose files become resources (name = file name without extension / .9).
    static final Set<String> FILE_TYPES = new HashSet<>(Arrays.asList(
        "anim", "animator", "color", "drawable", "font", "interpolator", "layout", "menu",
        "mipmap", "navigation", "raw", "transition", "xml"));
    // Values-XML element names accepted as resource types.
    static final Set<String> VALUE_TYPES = new HashSet<>(Arrays.asList(
        "string", "color", "dimen", "bool", "integer", "fraction", "plurals", "style", "attr",
        "drawable", "id", "layout", "mipmap", "raw", "xml", "menu", "anim", "animator",
        "interpolator", "transition", "font", "array", "styleable", "navigation"));
    static final Pattern ID_REF = Pattern.compile("@\\+?id/([A-Za-z0-9_.]+)");

    static final Map<String, LinkedHashSet<String>> syms = new TreeMap<>();
    static final Map<String, LinkedHashSet<String>> styleables = new LinkedHashMap<>();
    static final List<String> notes = new ArrayList<>();

    static String norm(String n) {
        return n.replace('.', '_').replace('-', '_').replace(':', '_');
    }

    static void add(String type, String rawName) {
        if (rawName == null || rawName.isEmpty()) return;
        String name = norm(rawName);
        if (!SourceVersion.isIdentifier(name) || SourceVersion.isKeyword(name)) {
            notes.add("skipped invalid identifier R." + type + "." + name);
            return;
        }
        syms.computeIfAbsent(type, k -> new LinkedHashSet<>()).add(name);
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 3) usage();
        String mode = args[0];
        Path outDir = Paths.get(args[1]);
        String pkg = args[2];
        if (mode.equals("app")) {
            List<Path> resDirs = new ArrayList<>();
            for (int i = 3; i < args.length; i++) {
                switch (args[i]) {
                    case "--res": resDirs.add(Paths.get(args[++i])); break;
                    case "--string": add("string", args[++i]); break;
                    case "--sym": { String[] p = args[++i].split(":", 2); if (p.length == 2) add(p[0], p[1]); break; }
                    default: usage();
                }
            }
            for (Path res : resDirs) scanRes(res);
            writeApp(outDir, pkg);
        } else if (mode.equals("rtxt")) {
            if (args.length < 4) usage();
            writeFromRtxt(outDir, pkg, Paths.get(args[3]));
        } else {
            usage();
        }
        for (String n : notes) System.err.println("RGen: " + n);
    }

    static void usage() {
        System.err.println("usage: RGen app <outDir> <package> --res <dir>... [--string <name>]... [--sym <type>:<name>]...");
        System.err.println("       RGen rtxt <outDir> <package> <R.txt>");
        System.exit(64);
    }

    static void scanRes(Path res) throws Exception {
        if (!Files.isDirectory(res)) {
            notes.add("resource dir does not exist: " + res);
            return;
        }
        List<Path> dirs;
        try (Stream<Path> s = Files.list(res)) {
            dirs = s.filter(Files::isDirectory).sorted().collect(Collectors.toList());
        }
        for (Path dir : dirs) {
            String dn = dir.getFileName().toString();
            String type = dn.contains("-") ? dn.substring(0, dn.indexOf('-')) : dn;
            List<Path> files;
            try (Stream<Path> s = Files.list(dir)) {
                files = s.filter(Files::isRegularFile).sorted().collect(Collectors.toList());
            }
            if (type.equals("values")) {
                for (Path f : files) if (f.toString().endsWith(".xml")) parseValues(f);
            } else if (FILE_TYPES.contains(type)) {
                for (Path f : files) {
                    String fn = f.getFileName().toString();
                    if (fn.startsWith(".")) continue;
                    int dot = fn.lastIndexOf('.');
                    String base = dot > 0 ? fn.substring(0, dot) : fn;
                    if (base.endsWith(".9")) base = base.substring(0, base.length() - 2);
                    add(type, base);
                }
            }
            for (Path f : files) if (f.toString().endsWith(".xml")) scanIds(f);
        }
    }

    static void parseValues(Path f) {
        Document doc;
        try {
            DocumentBuilderFactory dbf = DocumentBuilderFactory.newInstance();
            dbf.setNamespaceAware(false);
            dbf.setValidating(false);
            dbf.setExpandEntityReferences(false);
            try { dbf.setFeature("http://apache.org/xml/features/nonvalidating/load-external-dtd", false); } catch (Exception ignored) {}
            try { dbf.setFeature("http://xml.org/sax/features/external-general-entities", false); } catch (Exception ignored) {}
            try { dbf.setFeature("http://xml.org/sax/features/external-parameter-entities", false); } catch (Exception ignored) {}
            doc = dbf.newDocumentBuilder().parse(f.toFile());
        } catch (Exception e) {
            notes.add("could not parse " + f + ": " + e.getMessage());
            return;
        }
        Element root = doc.getDocumentElement();
        if (root == null) return;
        for (Node n = root.getFirstChild(); n != null; n = n.getNextSibling()) {
            if (!(n instanceof Element)) continue;
            Element e = (Element) n;
            String tag = e.getTagName();
            String name = e.getAttribute("name");
            switch (tag) {
                case "string-array":
                case "integer-array":
                case "array":
                    add("array", name);
                    break;
                case "declare-styleable": {
                    if (name.isEmpty()) break;
                    add("styleable", name);
                    LinkedHashSet<String> kids = styleables.computeIfAbsent(norm(name), k -> new LinkedHashSet<>());
                    for (Node c = e.getFirstChild(); c != null; c = c.getNextSibling()) {
                        if (c instanceof Element && ((Element) c).getTagName().equals("attr")) {
                            String an = ((Element) c).getAttribute("name");
                            if (an.isEmpty()) continue;
                            if (!an.contains(":")) add("attr", an);
                            kids.add(norm(an));
                        }
                    }
                    break;
                }
                case "item": {
                    String t = e.getAttribute("type");
                    if (t.isEmpty()) break;
                    if (t.equals("string-array") || t.equals("integer-array")) t = "array";
                    if (VALUE_TYPES.contains(t)) add(t, name); else notes.add("unknown item type '" + t + "' in " + f);
                    break;
                }
                case "public":
                case "eat-comment":
                case "skip":
                case "overlayable":
                    break;
                default:
                    if (name.isEmpty()) break;
                    if (VALUE_TYPES.contains(tag)) add(tag, name); else notes.add("ignored element <" + tag + " name=\"" + name + "\"> in " + f);
            }
        }
    }

    static void scanIds(Path f) throws IOException {
        String text = new String(Files.readAllBytes(f), StandardCharsets.UTF_8);
        Matcher m = ID_REF.matcher(text);
        while (m.find()) add("id", m.group(1));
    }

    static void writeApp(Path outDir, String pkg) throws IOException {
        // First pass: assign a unique value to every symbol (needed for styleable arrays).
        Map<String, Integer> values = new HashMap<>();
        int typeIdx = 1;
        for (Map.Entry<String, LinkedHashSet<String>> en : syms.entrySet()) {
            int i = 1;
            for (String name : en.getValue()) values.put(en.getKey() + "/" + name, 0x7f000000 | (typeIdx << 16) | i++);
            typeIdx++;
        }
        StringBuilder sb = new StringBuilder();
        sb.append("/* Generated by compilecheck RGen from the app's res/ directories. Values are arbitrary but unique. */\n");
        sb.append("package ").append(pkg).append(";\n\npublic final class R {\n    private R() {}\n");
        for (Map.Entry<String, LinkedHashSet<String>> en : syms.entrySet()) {
            String type = en.getKey();
            sb.append("    public static final class ").append(type).append(" {\n");
            sb.append("        private ").append(type).append("() {}\n");
            for (String name : en.getValue()) {
                if (type.equals("styleable")) {
                    List<String> kids = new ArrayList<>(styleables.getOrDefault(name, new LinkedHashSet<>()));
                    List<String> vals = new ArrayList<>();
                    for (String kid : kids) {
                        Integer v = values.get("attr/" + kid);
                        vals.add(v != null ? "0x" + Integer.toHexString(v) : "0x01010000");
                    }
                    sb.append("        public static final int[] ").append(name).append(" = { ").append(String.join(", ", vals)).append(" };\n");
                    int k = 0;
                    for (String kid : kids) {
                        sb.append("        public static final int ").append(name).append('_').append(kid).append(" = ").append(k++).append(";\n");
                    }
                } else {
                    sb.append("        public static final int ").append(name).append(" = 0x")
                      .append(Integer.toHexString(values.get(type + "/" + name))).append(";\n");
                }
            }
            sb.append("    }\n");
        }
        sb.append("}\n");
        write(outDir, pkg, sb.toString());
    }

    static void writeFromRtxt(Path outDir, String pkg, Path rtxt) throws IOException {
        Map<String, List<String>> decls = new TreeMap<>();
        for (String line : Files.readAllLines(rtxt, StandardCharsets.UTF_8)) {
            line = line.trim();
            if (line.isEmpty()) continue;
            String[] t = line.split("\\s+", 4);
            if (t.length < 4) continue;
            String jt = t[0], type = t[1], name = t[2], val = t[3].trim();
            if (!jt.equals("int") && !jt.equals("int[]")) continue;
            if (!SourceVersion.isIdentifier(name) || SourceVersion.isKeyword(name) || !SourceVersion.isIdentifier(type)) {
                notes.add("skipped R.txt entry " + type + "." + name);
                continue;
            }
            decls.computeIfAbsent(type, k -> new ArrayList<>())
                 .add("        public static final " + jt + " " + name + " = " + val + ";\n");
        }
        StringBuilder sb = new StringBuilder();
        sb.append("/* Generated by compilecheck RGen from ").append(rtxt.getFileName()).append(" of a library AAR. */\n");
        sb.append("package ").append(pkg).append(";\n\npublic final class R {\n    private R() {}\n");
        for (Map.Entry<String, List<String>> en : decls.entrySet()) {
            sb.append("    public static final class ").append(en.getKey()).append(" {\n");
            sb.append("        private ").append(en.getKey()).append("() {}\n");
            for (String d : en.getValue()) sb.append(d);
            sb.append("    }\n");
        }
        sb.append("}\n");
        write(outDir, pkg, sb.toString());
    }

    static void write(Path outDir, String pkg, String text) throws IOException {
        Path dir = outDir.resolve(pkg.replace('.', '/'));
        Files.createDirectories(dir);
        Files.write(dir.resolve("R.java"), text.getBytes(StandardCharsets.UTF_8));
    }
}
