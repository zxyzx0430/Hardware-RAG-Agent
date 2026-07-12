import fitz, re, sys
pdf = fitz.open(sys.argv[1])
patterns = sys.argv[2:]
for i in range(len(pdf)):
    t = pdf[i].get_text()
    hits = [p for p in patterns if re.search(p, t, re.I)]
    if hits:
        print(f'p{i+1}: {', '.join(hits)}')
pdf.close()
