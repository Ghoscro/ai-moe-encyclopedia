import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('build',ROOT/'tools/build.py')
build=importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

def example():
    return json.loads((ROOT/'examples/new-meme.json').read_text(encoding='utf-8'))

class ContributionTests(unittest.TestCase):
    def test_domain_migration_keeps_community_external_and_anchors_local(self):
        doc = '''<head><link rel="canonical" href="https://mickerbook.com/claude/"></head><a href="/">社区</a><a href="/ai-test/">测试</a><a href="/meme/#characters">图鉴</a><a href="/claude/#story">条目</a><a href="https://mickerbook.com/gpt/#story">旧百科链接</a>'''
        result = build.migrate_page(doc, 'claude/index.html')
        self.assertIn('href="https://mickerbook.com/"', result)
        self.assertIn('href="https://mickerbook.com/ai-test/"', result)
        self.assertIn('href="/#characters"', result)
        self.assertIn('href="/claude/#story"', result)
        self.assertIn('href="https://aiher.ai/gpt/#story"', result)
        self.assertIn('rel="canonical" href="https://aiher.ai/claude/"', result)
        self.assertEqual(result.count('rel="canonical"'), 1)
        self.assertNotIn('https://mickerbook.com/claude/', result)

    def test_original_and_sourced_rules(self):
        row=example()
        build.validate(row,row['id']+'.json')
        row['kind']='sourced'
        with self.assertRaises(ValueError): build.validate(row)
        row['sources']=[{'title':'原始公告','url':'https://example.com/announcement'}]
        build.validate(row)

    def test_rejects_unsafe_sources_paths_unknown_fields(self):
        for url in ['javascript:alert(1)','data:text/html,test','http://example.com','https://user:pass@example.com']:
            with self.subTest(url=url):
                row=example(); row['sources']=[{'title':'test','url':url}]
                with self.assertRaises(ValueError): build.validate(row)
        for ident in ['../escape','<script>','UPPER']:
            row=example(); row['id']=ident
            with self.assertRaises(ValueError): build.validate(row)
        row=example(); row['html']='<script>alert(1)</script>'
        with self.assertRaises(ValueError): build.validate(row)

    def test_new_entry_renders_and_is_searchable_without_script_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            shutil.copytree(ROOT/'site',root/'site')
            (root/'content/memes').mkdir(parents=True)
            row=example(); row['body']='这是一条虚构测试 </script><script>alert("xss")</script>'
            (root/'content/memes'/f'{row["id"]}.json').write_text(json.dumps(row,ensure_ascii=False),encoding='utf-8')
            output=build.build(root)
            home=(output/'meme/index.html').read_text(encoding='utf-8')
            target=(output/'gpt/index.html').read_text(encoding='utf-8')
            self.assertIn('id="'+row['id']+'"',target)
            self.assertIn('&lt;script&gt;',target)
            self.assertNotIn('<script>alert("xss")',target)
            self.assertNotIn('</script><script>alert',home)
            self.assertIn('\\u003c/script\\u003e',home)
            self.assertEqual(json.loads((output/'meme/community-manifest.json').read_text(encoding='utf-8'))['community_entries'],1)
            row['id']='another-id'
            (root/'content/memes/another-id.json').write_text(json.dumps(row),encoding='utf-8')
            with self.assertRaises(ValueError): build.entries(root)

    def test_legacy_ids_cannot_be_shadowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'site/meme').mkdir(parents=True); (root/'content/memes').mkdir(parents=True)
            data=[{'type':'meme','owner':'Codex 娘','title':'旧标题','href':'/codex/#existing-id'}]
            (root/'site/meme/index.html').write_text('<script id="search-data">'+json.dumps(data)+'</script>',encoding='utf-8')
            row=example(); row['id']='existing-id'
            (root/'content/memes/existing-id.json').write_text(json.dumps(row),encoding='utf-8')
            with self.assertRaises(ValueError): build.entries(root)

    def test_baseline_remains_available(self):
        expected=json.loads((ROOT/'content/baseline.json').read_text(encoding='utf-8'))['indexed_memes']
        self.assertEqual(sum(e.get('type')=='meme' for e in build.baseline()),expected)
        build.entries()

    def test_editorial_contributions_follow_original_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            shutil.copytree(ROOT/'site',root/'site')
            (root/'content/memes').mkdir(parents=True)
            row=example(); row['character']='gemini'
            (root/'content/memes'/f'{row["id"]}.json').write_text(json.dumps(row,ensure_ascii=False),encoding='utf-8')
            output=build.build(root)
            page=(output/'gemini/index.html').read_text(encoding='utf-8')
            self.assertIn('data-editorial-theme="gemini"',page)
            self.assertEqual(page.count('class="community-strip"'),1)
            self.assertLess(page.index('id="gemini-comic-04"'),page.index('class="community-strip"'))
            self.assertLess(page.index('class="community-strip"'),page.index('id="'+row['id']+'"'))
            self.assertLess(page.index('id="'+row['id']+'"'),page.index('</main>'))
            self.assertIn('/meme/contribute/',page[page.index('class="community-strip"'):])
            home=(output/'meme/index.html').read_text(encoding='utf-8')
            self.assertIn(row['id'],home)

if __name__=='__main__': unittest.main()
