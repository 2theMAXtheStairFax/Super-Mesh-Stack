import unittest

import wsgi_helper  # noqa: F401  (sets up the scratch app dir before web is used)
from wsgi_helper import call

import about


class AboutTest(unittest.TestCase):
    def test_page_lists_version_and_every_credit(self):
        status, _, html = call("/about")
        self.assertTrue(status.startswith("200"))
        self.assertIn(about.VERSION, html)
        for name, _, _, url in about.CREDITS:
            self.assertIn(name.replace("&", "&amp;"), html)
            self.assertIn(url, html)

    def test_byline_is_shown_on_the_page(self):
        self.assertEqual(about.BYLINE, "by 2theMAX")
        self.assertIn("<b>Super Mesh Stack</b> by 2theMAX", call("/about")[2])

    def test_tab_is_in_the_nav(self):
        self.assertIn('href="/about"', call("/about")[2])

    def test_credit_entries_are_well_formed(self):
        for name, role, lic, url in about.CREDITS:
            self.assertTrue(name and role, name)
            self.assertTrue(url.startswith("https://"), name)

    def test_screen_labels_fit_and_are_not_cut_mid_word(self):
        for name, *_ in about.CREDITS:
            label = about.short_name(name)
            self.assertLessEqual(len(label), 20, name)
            self.assertTrue(name.startswith(label), name)
        self.assertEqual(about.short_name("meshcore-cli / meshcore_py"), "meshcore-cli")
        self.assertEqual(about.short_name("Oswald, Bungee Hairline"), "Oswald")

    def test_external_links_do_not_leak_the_opener(self):
        html = call("/about")[2]
        self.assertEqual(html.count("target=_blank"), html.count("noopener noreferrer"))


if __name__ == "__main__":
    unittest.main()
