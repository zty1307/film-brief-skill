"""Optional headless DOCX preview. Never starts Office or sends desktop input."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docx',required=True)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--license',help='Optional valid Aspose.Words license supplied by the user')
    args=parser.parse_args()
    source=Path(args.docx).resolve()
    output=Path(args.output_dir).resolve()
    try:
        import aspose.words as aw
        from pdf2image import convert_from_path
        from pypdf import PdfReader
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        if args.license:
            license=aw.License()
            license.set_license(str(Path(args.license).resolve()))
        output.mkdir(parents=True,exist_ok=True)
        document=aw.Document(str(source))
        pdf=output/'preview.pdf'
        document.save(str(pdf))
        poppler=shutil.which('pdftoppm')
        if not poppler:
            raise RuntimeError('PDF已生成，但没有可用的后台Poppler，无法生成逐页图片')
        pages=convert_from_path(str(pdf),dpi=140,fmt='png',output_folder=str(output),
                                output_file='page',paths_only=True,poppler_path=str(Path(poppler).parent))
        if digest!=hashlib.sha256(source.read_bytes()).hexdigest():
            raise RuntimeError('源Word发生变化，停止验收')
        reader=PdfReader(str(pdf))
        limitations=['独立转换引擎的分页可能与不同版本的Microsoft Word略有差异']
        if not args.license:
            limitations.append('评估版预览保留试用标记，仅用于内部排版核对；没有移除标记或写回交付Word')
        result={'render_engine':'Aspose.Words Python via .NET','page_count':len(reader.pages),
                'source_docx':str(source),'source_sha256':digest,'source_unchanged':True,
                'preview_pdf':str(pdf),'page_images':pages,'limitations':limitations,
                'visual_review':'pending'}
        (output/'layout_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error':str(exc),'desktop_automation_used':False},ensure_ascii=False))
        return 2
    return 0


if __name__=='__main__':raise SystemExit(main())
