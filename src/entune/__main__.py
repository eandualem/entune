from entune.first_start import announce

announce()  # before the import below, which is what makes a cold start slow

from entune.cli import main  # noqa: E402

main()
