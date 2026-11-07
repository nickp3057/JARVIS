import unittest

from brain.download_batches import EpisodeBatcher


def torrent(name: str, progress: float) -> dict:
    return {"name": name, "progress": progress}


class EpisodeBatcherTests(unittest.TestCase):
    def test_movies_and_season_packs_are_said_at_once(self) -> None:
        b = EpisodeBatcher()
        self.assertEqual(b.completed("Iron.Man.2008.1080p.BluRay.x264-SPARKS"), "Iron Man has finished downloading, sir.")
        self.assertEqual(b.completed("Andor.S01.2160p.WEB-DL"), "Andor, season 1 has finished downloading, sir.")

    def test_episodes_wait_for_the_rest_of_the_show(self) -> None:
        b = EpisodeBatcher()
        self.assertIsNone(b.completed("Silo.S01E01.1080p.WEB", now=0))
        self.assertIsNone(b.completed("Silo.S01E02.1080p.WEB", now=10))
        still = [torrent("Silo.S01E01.1080p.WEB", 1), torrent("Silo.S01E03.1080p.WEB", 0.4),
                 torrent("Iron.Man.2008.1080p", 0.2)]
        self.assertEqual(b.flush(still, now=20), [])
        self.assertIsNone(b.completed("Silo.S01E03.1080p.WEB", now=30))
        done = [torrent("Silo.S01E03.1080p.WEB", 1), torrent("Iron.Man.2008.1080p", 0.2)]
        self.assertEqual(b.flush(done, now=40), ["3 episodes of Silo have finished downloading, sir."])
        self.assertEqual(b.flush(done, now=50), [])

    def test_a_lone_episode_keeps_its_name(self) -> None:
        b = EpisodeBatcher()
        b.completed("The.Office.US.S02E03.720p.WEB-DL", now=0)
        self.assertEqual(b.flush([], now=1), ["The Office US, season 2, episode 3 has finished downloading, sir."])

    def test_a_stuck_episode_cant_hold_the_rest_forever(self) -> None:
        b = EpisodeBatcher()
        b.completed("Silo.S01E01.1080p.WEB", now=0)
        stuck = [torrent("Silo.S01E09.1080p.WEB", 0.1)]
        self.assertEqual(b.flush(stuck, now=600), [])
        self.assertEqual(b.flush(stuck, now=1801), ["Silo, season 1, episode 1 has finished downloading, sir."])


if __name__ == "__main__":
    unittest.main()
