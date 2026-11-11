#!/usr/bin/env python3
"""
patch_ram_alignment.py - 16KB RAM Alignment & Range Fix for HakiOS on SM6225 (Qualcomm Bengal)

Ensures:
1. In osfmk/arm/arm_vm_init.c (or osfmk/arm64/arm_vm_init.c):
   At the end of arm_vm_init(), avail_start and first_avail are rounded up to
   PAGE_SIZE (16KB granule) using full 64-bit (pmap_paddr_t)PAGE_MASK to avoid
   high-bit truncation and assertion failures before handoff to pmap_bootstrap()
   and initialize_ram_ranges().

2. In osfmk/arm/pmap/pmap.c:initialize_ram_ranges() & pmap_free_pages_span():
   - Fail-safe VA to PA normalization: if first_avail >= 0xfffffff000000000ULL,
     translates via ml_static_vtop() and paints red band at row 284.
   - Enforces 16KB alignment without panicking assertions.
   - Clamps invalid ranges (first < gPhysBase || end <= first) to prevent underflow.
   - Instruments pmap_free_pages_span() with visual diagnostic bands (rows 276, 280, 284, 288, 292, 294).
   - Eliminates panicking asserts in pmap_next_page().
"""

import os
import sys
import re

def patch_arm_vm_init(xnu_dir):
    candidates = [
        os.path.join(xnu_dir, "osfmk", "arm", "arm_vm_init.c"),
        os.path.join(xnu_dir, "osfmk", "arm64", "arm_vm_init.c"),
    ]
    target_path = None
    for c in candidates:
        if os.path.isfile(c):
            target_path = c
            break

    if not target_path:
        print(f"  [ERROR] Neither osfmk/arm/arm_vm_init.c nor osfmk/arm64/arm_vm_init.c found in {xnu_dir}")
        return False

    with open(target_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    original_content = content

    rel_path = os.path.relpath(target_path, xnu_dir).replace("\\", "/")

    marker = "/* hakios: ensure avail_start and first_avail are rounded up to 16KB PAGE_SIZE */"
    alignment = re.compile(
        r'avail_start\s*=\s*\(\s*avail_start\s*\+\s*PAGE_MASK\s*\)\s*'
        r'&\s*~\s*\(\s*pmap_paddr_t\s*\)\s*PAGE_MASK\s*;'
    )
    first_assignment = re.compile(r'first_avail\s*=\s*avail_start\s*;')

    # The marker alone is not proof of a complete patch: an earlier partial run
    # could have written it before failing to match one of the assignments.
    function_span = c_function_body_span(content, "arm_vm_init")
    if function_span is None:
        print(f"  [ERROR] {rel_path}: expected one arm_vm_init definition; source left unchanged")
        return False
    body_start, body_end = function_span
    body = content[body_start:body_end]
    first_matches = list(first_assignment.finditer(body))
    if len(first_matches) != 1:
        print(f"  [ERROR] {rel_path}: expected one first_avail assignment in arm_vm_init; source left unchanged")
        return False
    first_match = first_matches[0]
    later_avail_writes = re.search(
        r'(?<![A-Za-z0-9_])avail_start[ \t]*(?:=(?!=)|\+=|-=|\+\+|--)',
        body[first_match.end():]
    )
    if later_avail_writes:
        print(f"  [ERROR] {rel_path}: avail_start changes after first_avail handoff; source left unchanged")
        return False

    marker_match = re.search(re.escape(marker), body)
    alignment_matches = list(alignment.finditer(body))
    alignment_before_first = next(
        (m for m in alignment_matches if m.end() <= first_match.start()), None
    )
    if marker_match and alignment_before_first and marker_match.start() < alignment_before_first.start():
        print(f"  [ALREADY PATCHED] {rel_path}: final arm_vm_init handoff is aligned")
        return True

    if alignment_before_first is None:
        line_start = body.rfind("\n", 0, first_match.start()) + 1
        indent = re.match(r'[ \t]*', body[line_start:first_match.start()]).group(0)
        replacement = (
            f"{indent}{marker}\n"
            f"{indent}if (avail_start >= 0xffff000000000000ULL) avail_start = ml_static_vtop(avail_start);\n"
            f"{indent}avail_start = (avail_start + PAGE_MASK) & ~(pmap_paddr_t)PAGE_MASK;\n"
            f"{indent}first_avail = avail_start;"
        )
        body = body[:line_start] + replacement + body[first_match.end():]
    elif marker_match is None or marker_match.start() > alignment_before_first.start():
        line_start = body.rfind("\n", 0, alignment_before_first.start()) + 1
        indent = re.match(r'[ \t]*', body[line_start:alignment_before_first.start()]).group(0)
        body = body[:line_start] + f"{indent}{marker}\n" + body[line_start:]

    content = content[:body_start] + body + content[body_end:]

    if content != original_content:
        with open(target_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        print(f"  [OK] {rel_path}: patched avail_start and first_avail 16KB alignment")
        return True
    print(f"  [ALREADY PATCHED] {rel_path}: avail_start and first_avail alignment already present")
    return True


def c_function_body_span(content, name):
    """
    Finds the (body_start, body_end) character span for the definition of function `name`.
    `body_start` is the index of the opening '{'.
    `body_end` is the index immediately following the closing '}'.
    Returns None if 0 or >1 definitions are found.
    Handles all C comment styles (/* */, //), strings, character literals,
    nested braces, multiline declarations, and attributes without recursion.
    """
    NON_TYPE_KEYWORDS = {
        "return", "goto", "break", "continue", "case", "default",
        "sizeof", "typeof", "__typeof__", "if", "else", "while", "for", "switch", "do"
    }

    definitions = []
    pos = 0
    length = len(content)
    state = "code"
    brace_depth = 0

    while pos < length:
        ch = content[pos]
        nxt = content[pos + 1] if pos + 1 < length else ""

        if state == "code":
            if ch == "/" and nxt == "*":
                state = "comment"
                pos += 2
                continue
            if ch == "/" and nxt == "/":
                state = "line_comment"
                pos += 2
                continue
            if ch == '"':
                state = "string"
                pos += 1
                continue
            if ch == "'":
                state = "char"
                pos += 1
                continue

            if ch == "{":
                brace_depth += 1
                pos += 1
                continue
            if ch == "}":
                if brace_depth > 0:
                    brace_depth -= 1
                pos += 1
                continue

            # Only at file scope (brace_depth == 0) can a function definition begin
            if brace_depth == 0 and (ch.isalpha() or ch == "_"):
                start_id = pos
                while pos < length and (content[pos].isalnum() or content[pos] == "_"):
                    pos += 1
                ident = content[start_id:pos]

                if ident == name:
                    # Check what precedes this identifier (must not be (* or non-type keywords)
                    pre_idx = start_id - 1
                    while pre_idx >= 0 and content[pre_idx].isspace():
                        pre_idx -= 1
                    # Skip preceding block comments backwards if any
                    while pre_idx >= 1 and content[pre_idx - 1:pre_idx + 1] == "*/":
                        cb_start = content.rfind("/*", 0, pre_idx - 1)
                        if cb_start != -1:
                            pre_idx = cb_start - 1
                            while pre_idx >= 0 and content[pre_idx].isspace():
                                pre_idx -= 1
                        else:
                            break

                    # Collect preceding token
                    tok_before = ""
                    if pre_idx >= 0 and (content[pre_idx].isalnum() or content[pre_idx] == "_"):
                        end_t = pre_idx + 1
                        while pre_idx >= 0 and (content[pre_idx].isalnum() or content[pre_idx] == "_"):
                            pre_idx -= 1
                        tok_before = content[pre_idx + 1:end_t]
                    elif pre_idx >= 0 and content[pre_idx] == "*":
                        tok_before = "*"

                    if tok_before and tok_before not in NON_TYPE_KEYWORDS and (pre_idx < 0 or content[pre_idx] != "("):
                        # Now check if followed by '(' parameter list
                        p_pos = pos
                        while p_pos < length and content[p_pos].isspace():
                            p_pos += 1

                        if p_pos < length and content[p_pos] == "(":
                            # Scan parameter list to matching ')'
                            p_depth = 0
                            p_state = "code"
                            close_p = None
                            c_scan = p_pos

                            while c_scan < length:
                                c_ch = content[c_scan]
                                c_nxt = content[c_scan + 1] if c_scan + 1 < length else ""

                                if p_state == "code":
                                    if c_ch == "/" and c_nxt == "*":
                                        p_state = "comment"
                                        c_scan += 2
                                        continue
                                    if c_ch == "/" and c_nxt == "/":
                                        p_state = "line_comment"
                                        c_scan += 2
                                        continue
                                    if c_ch == '"':
                                        p_state = "string"
                                        c_scan += 1
                                        continue
                                    if c_ch == "'":
                                        p_state = "char"
                                        c_scan += 1
                                        continue
                                    if c_ch == "(":
                                        p_depth += 1
                                    elif c_ch == ")":
                                        p_depth -= 1
                                        if p_depth == 0:
                                            close_p = c_scan
                                            break
                                elif p_state == "comment":
                                    if c_ch == "*" and c_nxt == "/":
                                        p_state = "code"
                                        c_scan += 2
                                        continue
                                elif p_state == "line_comment":
                                    if c_ch == "\n":
                                        p_state = "code"
                                elif p_state in ("string", "char"):
                                    if c_ch == "\\":
                                        c_scan += 2
                                        continue
                                    if (p_state == "string" and c_ch == '"') or (p_state == "char" and c_ch == "'"):
                                        p_state = "code"
                                c_scan += 1

                            if close_p is not None:
                                # Now skip whitespace/comments/attributes to find opening '{'
                                a_pos = close_p + 1
                                a_state = "code"
                                body_start = None

                                while a_pos < length:
                                    ach = content[a_pos]
                                    anxt = content[a_pos + 1] if a_pos + 1 < length else ""

                                    if a_state == "code":
                                        if ach.isspace():
                                            a_pos += 1
                                            continue
                                        if ach == "/" and anxt == "*":
                                            a_state = "comment"
                                            a_pos += 2
                                            continue
                                        if ach == "/" and anxt == "/":
                                            a_state = "line_comment"
                                            a_pos += 2
                                            continue
                                        if ach == "{":
                                            body_start = a_pos
                                            break
                                        elif ach == ";" or ach == ",":
                                            break
                                        else:
                                            # Skip attribute/specifier token
                                            a_pos += 1
                                            continue
                                    elif a_state == "comment":
                                        if ach == "*" and anxt == "/":
                                            a_state = "code"
                                            a_pos += 2
                                            continue
                                        a_pos += 1
                                    elif a_state == "line_comment":
                                        if ach == "\n":
                                            a_state = "code"
                                        a_pos += 1

                                if body_start is not None:
                                    # Now find matching '}' for this function body
                                    b_pos = body_start
                                    b_depth = 0
                                    b_state = "code"
                                    body_end = None

                                    while b_pos < length:
                                        bch = content[b_pos]
                                        bnxt = content[b_pos + 1] if b_pos + 1 < length else ""

                                        if b_state == "code":
                                            if bch == "/" and bnxt == "*":
                                                b_state = "comment"
                                                b_pos += 2
                                                continue
                                            if bch == "/" and bnxt == "/":
                                                b_state = "line_comment"
                                                b_pos += 2
                                                continue
                                            if bch == '"':
                                                b_state = "string"
                                                b_pos += 1
                                                continue
                                            if bch == "'":
                                                b_state = "char"
                                                b_pos += 1
                                                continue
                                            if bch == "{":
                                                b_depth += 1
                                            elif bch == "}":
                                                b_depth -= 1
                                                if b_depth == 0:
                                                    body_end = b_pos + 1
                                                    break
                                        elif b_state == "comment":
                                            if bch == "*" and bnxt == "/":
                                                b_state = "code"
                                                b_pos += 2
                                                continue
                                        elif b_state == "line_comment":
                                            if bch == "\n":
                                                b_state = "code"
                                        elif b_state in ("string", "char"):
                                            if bch == "\\":
                                                b_pos += 2
                                                continue
                                            if (b_state == "string" and bch == '"') or (b_state == "char" and bch == "'"):
                                                b_state = "code"
                                        b_pos += 1

                                    if body_end is not None:
                                        definitions.append((body_start, body_end))
                                        pos = body_end
                                        continue
                continue

            pos += 1
            continue

        elif state == "comment":
            if ch == "*" and nxt == "/":
                state = "code"
                pos += 2
                continue
            pos += 1
        elif state == "line_comment":
            if ch == "\n":
                state = "code"
            pos += 1
        elif state in ("string", "char"):
            if ch == "\\":
                pos += 2
                continue
            if (state == "string" and ch == '"') or (state == "char" and ch == "'"):
                state = "code"
            pos += 1

    if len(definitions) != 1:
        return None
    return definitions[0]


def patch_pmap(xnu_dir):
    target_path = os.path.join(xnu_dir, "osfmk", "arm", "pmap", "pmap.c")
    if not os.path.isfile(target_path):
        print(f"  [ERROR] osfmk/arm/pmap/pmap.c not found in {xnu_dir}")
        return False

    with open(target_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    rel_path = os.path.relpath(target_path, xnu_dir).replace("\\", "/")

    replacement = """static void
initialize_ram_ranges(void)
{
	extern void hk_draw_band(uint32_t color, uint32_t row_start, uint32_t row_count);
	hk_draw_band(0x00FFFFFF, 250, 2); /* White: entered initialize_ram_ranges */

	pmap_paddr_t first = first_avail;
	pmap_paddr_t end   = avail_end;

	/* hakios/claude: normalize first_avail if passed as kernel VA */
	if (first >= 0xffff000000000000ULL) {
		first = ml_static_vtop((vm_offset_t)first);
		hk_draw_band(0x00FF0000, 252, 2); /* Red: was a kernel VA! */
	}

	first = (first + PAGE_MASK) & ~(pmap_paddr_t)PAGE_MASK;
	end  &= ~(pmap_paddr_t)PAGE_MASK;

	/* hakios: SM6225 Qualcomm firmware carve-outs (TZ/Hyp/SMEM ~0x85800000..0x86400000) */
	if (first < 0x85800000ULL && end > 0x85800000ULL) {
		end = 0x85800000ULL; /* Protect TZ/Hyp firmware carve-out from SError */
	}

	if (first < gPhysBase || end <= first) {
		hk_draw_band(0x00FF0000, 254, 2); /* Red: bad range */
		first = end;                      /* clamp: zero pages, no underflow */
	}

	first_avail      = first;
	avail_end        = end;
	avail_page_count = atop(end - first);
	need_ram_ranges_init = false;

	hk_draw_band(0x0000FF00, 256, 2); /* Lime: initialize_ram_ranges done */
}

unsigned int
pmap_free_pages(
	void)
{
	if (need_ram_ranges_init) {
		initialize_ram_ranges();
	}
	return avail_page_count;
}

unsigned int
pmap_free_pages_span(
	void)
{
	extern void hk_draw_band(uint32_t color, uint32_t row_start, uint32_t row_count);
	hk_draw_band(0x0000FFFF, 258, 2); /* Cyan: entered pmap_free_pages_span */

	if (need_ram_ranges_init) {
		initialize_ram_ranges();
	}

	hk_draw_band(0x00FFD700, 260, 2); /* Gold: span computed */
	return (unsigned int)atop(avail_end - first_avail);
}


boolean_t
pmap_next_page_hi(
	ppnum_t            * pnum,
	__unused boolean_t might_free)
{
	return pmap_next_page(pnum);
}


boolean_t
pmap_next_page(
	ppnum_t *pnum)
{
	if (need_ram_ranges_init) {
		initialize_ram_ranges();
	}

	if (first_avail != avail_end) {
		*pnum = (ppnum_t)atop(first_avail);
		first_avail += PAGE_SIZE;
		if (avail_page_count > 0) {
			--avail_page_count;
		}
		return TRUE;
	}
	return FALSE;
}"""

    names = (
        "initialize_ram_ranges",
        "pmap_free_pages",
        "pmap_free_pages_span",
        "pmap_next_page_hi",
        "pmap_next_page",
    )
    replacement_spans = {name: c_function_body_span(replacement, name) for name in names}
    source_spans = {name: c_function_body_span(content, name) for name in names}
    if any(span is None for span in replacement_spans.values()):
        print(f"  [ERROR] {rel_path}: replacement function extraction failed; source left unchanged")
        return False

    if any(span is None for span in source_spans.values()):
        if "/* hakios/claude: normalize first_avail if passed as kernel VA */" in content:
            print(f"  [ERROR] {rel_path}: partial/ambiguous patch detected; source left unchanged")
        else:
            print(f"  [ERROR] {rel_path}: expected one definition of every target function; source left unchanged")
        return False

    if "/* hakios/claude: normalize first_avail if passed as kernel VA */" in content:
        markers = (
            "hk_draw_band(0x0000FFFF, 258, 2)",
            "hk_draw_band(0x00FFD700, 260, 2)",
            "first_avail += PAGE_SIZE;",
        )
        if all(marker in content for marker in markers):
            if re.search(r'static\s+(bool|boolean_t)\s+need_ram_ranges_init', content):
                content = re.sub(r'static\s+(bool|boolean_t)\s+need_ram_ranges_init', r'\1 need_ram_ranges_init', content)
                with open(target_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(content)
            print(f"  [ALREADY PATCHED] {rel_path}: RAM range functions already patched")
            return True
        print(f"  [ERROR] {rel_path}: partial patch detected; source left unchanged")
        return False

    edits = []
    for name in names:
        source_start, source_end = source_spans[name]
        replacement_start, replacement_end = replacement_spans[name]
        edits.append((source_start, source_end, replacement[replacement_start:replacement_end]))
    for start, end, body in sorted(edits, reverse=True):
        content = content[:start] + body + content[end:]

    # Make need_ram_ranges_init non-static so it can be observed across translation units (e.g. vm_resident.c)
    content = re.sub(r'static\s+(bool|boolean_t)\s+need_ram_ranges_init', r'\1 need_ram_ranges_init', content)

    with open(target_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print(f"  [OK] {rel_path}: patched RAM range functions individually")
    return True


def main():
    if len(sys.argv) < 2:
        print("Usage: patch_ram_alignment.py <xnu_src_dir>")
        sys.exit(1)

    xnu_dir = sys.argv[1]
    if not os.path.isdir(xnu_dir):
        print(f"Error: Directory '{xnu_dir}' does not exist.")
        sys.exit(1)

    print(f"=== Applying 16KB RAM Alignment & Range Patch to {xnu_dir} ===")
    ok_vm = patch_arm_vm_init(xnu_dir)
    ok_pmap = patch_pmap(xnu_dir)

    if ok_vm and ok_pmap:
        print("=== 16KB RAM Alignment Patch applied successfully! ===")
        sys.exit(0)
    else:
        print("=== [FAIL] One or more patches failed to apply ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
