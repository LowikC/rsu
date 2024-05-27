# RSU

This project is used to facilitate tax declaration for french RSU.

## Installation


## Usage

You need to download the data from the Schwab website.
- Login to your Schwab account
- Go to Account -> History
- Select "Equity Award"
- Select "Previous 4 Years" or enter a custom range that includes the period included in your tax declaration
- Click Export (top left) and choose the JSON format

Then, run the script, by providing the path to the Schwab data, the fiscal year and a directory to write the results
```bash
python rsu.py --schwab_json=EquityAwardsCenter_Transactions_20240208190934.json --year=2023 --output_dir=.
```

You'll find 3 files in the output directory:
- rsu_YYYY.csv is a TSV file containing all the details on the sales
- rsu_tax_estimate_YYYY contains the tax estimation
- rsu_tax_instructions_YYYY contains the instructions to do your tax declaration (in french)

