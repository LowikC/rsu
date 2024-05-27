# RSU

This project is used to facilitate tax declaration for french RSU.

## Installation

Create an environment for the project and install the requirements.

```bash
git clone git@github.com:LowikC/rsu.git rsu
cd rsu
conda create -n rsu python=3.9
conda activate rsu
pip install -r requirements.txt
```

## Usage

You need to download your data from the Schwab website.
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
- `rsu_YYYY.csv` is a TSV file containing all the details on the sales
- `rsu_tax_estimate_YYYY` contains the tax estimation
- `rsu_tax_instructions_YYYY` contains the instructions to do your tax declaration (in french)


## Known issues

Some rules for the declaration are not perfectly clear to me (even after reading the official instructions many times).  

I made the following assumptions:
- we can process sales line by line (one line = a unique (vest date, sale date)), for example, subtract the capital loss from acquisition gain for each line.
- a line with capital gain = 0 doesn't need to be declared in the form 2074
- we apply the average tax relief (computed over all sales) on the acquisition gain below 300K. The tax relief can't be apply on the acquisition gain above 300K EUR.
- for tax estimation, I didn't take into account the "contribution exceptionnelle sur les hauts revenus" (exceptional contribution on high incomes) as it requires additional calculations and is beyond the scope of this script.
- If for a line, the capital loss is superior to the acquisition gain, the remaining capital loss (after cancelling the acquisition gain) will not be subtracted from the acquisition gain on other lines (I don't know if it's possible). And it won't be declared in the form 2074 neither.
- There might be small discrepancies between what is computed in your tax declaration in case 3VG after filling the form 2074, and the value returned by the tool: this is due to the rounded values that are put in the form 2074, while the script computes the exact value.