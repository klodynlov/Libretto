"""
Fuzz déterministe du parseur MIDI.

Contrat public vérifié ici : quel que soit l'octet qu'on lui donne,
`parse_midi_bytes` soit réussit, soit lève `ValueError`. Toute autre
exception (IndexError sur un fichier tronqué, struct.error, KeyError…)
est un bug : l'appelant — notamment le serveur HTTP, qui traduit
ValueError en 400 et le reste en 500 — ne peut pas s'en protéger.

Les entrées sont tirées d'un `random.Random(SEED)` local (jamais le
random global) : un échec est donc reproductible à l'identique.
4 familles × 200 cas = 800 entrées, pour ~1 s d'exécution.
"""

import random
import tempfile
import unittest
from pathlib import Path

from libretto.midi import parse_midi_bytes, write_midi

SEED = 20260720
CAS_PAR_FAMILLE = 200


def _apercu(data: bytes, limite: int = 80) -> str:
    """Hex tronqué, pour qu'un échec soit rejouable sans deviner l'entrée."""
    hexa = data.hex()
    return hexa if len(hexa) <= limite else f"{hexa[:limite]}… (+{len(hexa) - limite})"


def _midi_valide() -> bytes:
    """Un vrai SMF, fabriqué avec l'écrivain maison : 2 pistes, marqueurs et
    changement de tempo, donc meta / note-on / note-off / EOT sont couverts."""
    melodie = [(i * 0.5, 0.5, 60 + (i * 5) % 24, 70 + i % 40, 0) for i in range(48)]
    basse = [(float(i), 1.0, 36 + (i * 7) % 12, 90, 1) for i in range(24)]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fuzz.mid"
        write_midi(path, [melodie, basse], ppq=480, bpm=112,
                   markers=[(0.0, "Intro"), (8.0, "Refrain")],
                   tempo_changes=[(8.0, 128.0)])
        return path.read_bytes()


def _entete_mthd(rng: random.Random) -> bytes:
    """En-tête MThd bien formé mais aux champs arbitraires (parfois SMPTE)."""
    return (b"MThd" + (6).to_bytes(4, "big")
            + rng.randrange(3).to_bytes(2, "big")          # format
            + rng.randrange(1, 6).to_bytes(2, "big")       # nombre de pistes
            + rng.randrange(0x10000).to_bytes(2, "big"))   # division


class TestFuzzParseurMidi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.valide = _midi_valide()

    def _verifie(self, data: bytes, etiquette: str) -> None:
        """Le contrat : succès ou ValueError, jamais une exception brute."""
        try:
            parse_midi_bytes(data, origin="<fuzz>")
        except ValueError:
            pass
        except Exception as exc:  # noqa: BLE001 — c'est précisément la cible
            self.fail(
                f"{etiquette} : exception non contractuelle "
                f"{type(exc).__name__}: {exc}\n"
                f"  entrée ({len(data)} octets) = {_apercu(data)}"
            )

    def test_corpus_valide_se_parse(self):
        # Garde-fou : si le corpus de base ne parsait pas, le fuzz ne testerait rien.
        md = parse_midi_bytes(self.valide, origin="<fuzz>")
        self.assertEqual(md.ppq, 480)
        self.assertGreater(len(md.notes), 50)

    def test_octets_aleatoires(self):
        rng = random.Random(SEED)
        for n in range(CAS_PAR_FAMILLE):
            data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 257)))
            self._verifie(data, f"aleatoire#{n}")

    def test_entete_valide_puis_garbage(self):
        # Fait passer la garde MThd : le fuzz atteint la boucle de pistes.
        rng = random.Random(SEED + 1)
        for n in range(CAS_PAR_FAMILLE):
            corps = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 129)))
            if n % 3 == 0:  # amorce un chunk MTrk à longueur mensongère
                corps = b"MTrk" + rng.randrange(0x1000000).to_bytes(4, "big") + corps
            self._verifie(_entete_mthd(rng) + corps, f"entete+garbage#{n}")

    def test_troncatures(self):
        # Le cas qui cassait vraiment : un vrai MIDI coupé n'importe où.
        rng = random.Random(SEED + 2)
        total = len(self.valide)
        longueurs = (list(range(total)) if total <= CAS_PAR_FAMILLE
                     else sorted(rng.sample(range(total), CAS_PAR_FAMILLE)))
        for taille in longueurs:
            self._verifie(self.valide[:taille], f"tronque@{taille}")

    def test_octets_flippes(self):
        rng = random.Random(SEED + 3)
        for n in range(CAS_PAR_FAMILLE):
            buf = bytearray(self.valide)
            for _ in range(rng.randint(1, 5)):
                pos = rng.randrange(len(buf))
                if n % 2:
                    buf[pos] ^= 1 << rng.randrange(8)   # un bit
                else:
                    buf[pos] = rng.randrange(256)       # un octet entier
            self._verifie(bytes(buf), f"flip#{n}")


if __name__ == "__main__":
    unittest.main()
