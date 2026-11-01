import unittest

from integrations.qbittorrent import friendly_name


class FriendlyReleaseNameTests(unittest.TestCase):
    def test_names_read_like_a_person_would_say_them(self) -> None:
        cases = {
            "Iron.Man.2008.1080p.BluRay.x264-SPARKS": "Iron Man",
            "The.Office.US.S02E03.720p.WEB-DL.x264": "The Office US, season 2, episode 3",
            "Andor.S01.2160p.DSNP.WEB-DL": "Andor, season 1",
            "Spider-Man Into the Spider-Verse (2018) [1080p] [YTS.MX]": "Spider-Man Into the Spider-Verse",
            "Blade.Runner.2049.2017.2160p.UHD.BluRay.x265-TERMiNAL": "Blade Runner 2049",
            "1917.2019.1080p.mkv": "1917",
            "[SubsPlease] Frieren - 12 (1080p) [ABC123].mkv": "Frieren, episode 12",
            "Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1.Atmos.DV.HDR10.H.265": "Dune Part Two",
            "Arrival": "Arrival",
        }
        for release, spoken in cases.items():
            self.assertEqual(friendly_name(release), spoken, release)

    def test_falls_back_to_the_raw_name(self) -> None:
        self.assertEqual(friendly_name(""), "")
        self.assertEqual(friendly_name("[YTS]"), "[YTS]")


if __name__ == "__main__":
    unittest.main()
