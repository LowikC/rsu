import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import click
import pandas as pd
import requests


class ExchangeRateData:
    def __init__(self, exchange_rate_csv: Optional[Path]):
        self.exchange_rate_csv = exchange_rate_csv
        self.usd_change_rate_by_day = {}
        if not self.exchange_rate_csv or not self.exchange_rate_csv.is_file():
            self._download_exchange_rate_data()
        self._load_exchange_rate_data()

    def _download_exchange_rate_data(self):
        # This link is given on this page https://webstat.banque-france.fr/fr/questions-frequentes/
        # It contains the exchange rates for many currencies, including EUR to USD, starting from 1999.
        url = "https://webstat.banque-france.fr/export/csv-columns/fr/selection/5385698"
        response = requests.get(url)
        if response.status_code == 200:
            self.exchange_rate_csv = Path("exchange_rate.csv")
            with open(self.exchange_rate_csv, "wb") as file:
                file.write(response.content)
            print("Downloaded exchange rate data to exchange_rate.csv")
        else:
            raise FileNotFoundError(
                "Failed to download the exchange rate data. Try downloading the file manually."
            )

    def _load_exchange_rate_data(self):
        # The format of this file is a bit weird.
        # The first 5 rows are not useful in our case.
        # The first column is the date in the format "dd/mm/YYYY", and it's called "Titre :"
        df = pd.read_csv(self.exchange_rate_csv, sep=";")
        usd_column = "Dollar des Etats-Unis (USD)"
        df_usd = df[["Titre :", usd_column]]
        df_usd = df_usd.iloc[5:]
        # The column contains the exchange rate as a string of format "1,2345"
        # and "-" during the week-ends. We replace the "-" with the last known value.
        df_usd[usd_column] = df_usd[usd_column].replace("-", method="bfill")
        df_usd[usd_column] = df_usd[usd_column].str.replace(",", ".").astype(float)

        for _, row in df_usd.iterrows():
            date_str = row["Titre :"]
            usd = row[usd_column]
            self.usd_change_rate_by_day[date_str] = usd

    def get_euro_dollar_rate(self, date: datetime) -> float:
        """
        Retrieves the EUR to USD exchange rate for a given date from a CSV file.

        Args:
            date (datetime): The date for which the exchange rate is requested.

        Returns:
            float: The EUR to USD exchange rate for the specified date.

        Raises:
            KeyError: If the exchange rate data is not available for this date.
        """
        # The date is in the format "YYYY-MM-DD" in the original csv data
        date_str = date.strftime("%Y-%m-%d")
        return self.usd_change_rate_by_day[date_str]


@dataclass
class TransactionDetails:
    # Number of shares sold
    num_shares: int
    # Vesting (Acquisition) Date
    vest_date: datetime
    # Price per share at vesting/acquisition in USD
    vest_price_usd: float
    # Sale Date (Cession)
    sale_date: datetime
    # Sale Price per share
    sale_price_usd: float


@dataclass
class TransactionDetailsProcessed:
    # Number of shares sold
    num_shares: int
    # Vesting (Acquisition) Date
    vest_date: datetime
    # Price per share at vesting/acquisition in USD
    vest_price_usd: float
    # Sale Date (Cession)
    sale_date: datetime
    # Sale Price per share
    sale_price_usd: float
    # Exchange rate EUR -> USD for the vesting date
    vest_exchange_rate: float
    # Price per share at vesting/acquisition in EUR
    vest_price_eur: float
    # Exchange rate EUR -> USD for the sale date
    sale_exchange_rate: float
    # Price per share at sale in EUR
    sale_price_eur: float
    # Capital gain in EUR (sale price - vest price) per share
    capital_gain_eur: float
    # Total vest gain in EUR (Acquisition gain)
    total_vest_gain_eur: float
    # Total capital gain in EUR
    total_capital_gain_eur: float
    # Total sale price in EUR
    total_sale_price_eur: float
    # How long the shares were held before sale, between vesting and sale
    detention: timedelta
    # Whether the transaction is eligible for tax relief at 50%
    eligible_for_tax_relief_50p: bool
    # Whether the transaction is eligible for tax relief at 65%
    eligible_for_tax_relief_65p: bool
    # Tax relief amount in EUR
    taxe_relief_eur: float
    # Corrected total acquistion gain in EUR, after removing capital losses
    total_corrected_vest_gain_eur: float
    # Corrected total capital gain in EUR, after removing capital losses and applying tax relief
    total_corrected_capital_gain_eur: float


@dataclass
class TaxSummary:
    # Total acquisition gain over all transactions
    total_vest_gain_eur: float
    # Total capital gain over all transactions
    total_capital_gain_eur: float
    # Total sale price over all transactions (ie what you should have received on your bank account)
    total_sale_price_eur: float
    # Total tax relief over all transactions
    total_tax_relief_eur: float
    # Total acquisition gain over all transactions, after removing capital losses
    total_corrected_vest_gain_eur: float
    # Total capital gain over all transactions, after removing capital losses
    total_corrected_capital_gain_eur: float

    # Social contributions on the corrected acquisition gain
    social_contributions_on_vest_gain: float
    # Tax on the on the corrected acquistion gain
    tax_on_vest_gain: float
    # Tax on the corrected capital gain
    tax_on_capital_gain: float
    # Total tax to be paid
    total_tax: float
    # Average tax rate over all transactions
    total_tax_rate: float


def convert_schwab_float_format(s: str) -> float:
    # Format is $XXX,XXX.XX
    return float(s.replace("$", "").replace(",", ""))


def load_transactions_details(schwab_json: str, year: int):
    """
    Load and parse transaction details from a Schwab JSON file for a specific year.

    Args:
        schwab_json (str): The path to the Schwab JSON file.
        year (int): The year for which to retrieve the transactions details.

    Returns:
        list: A list of TransactionDetails objects containing the parsed transaction details.

    """
    with open(schwab_json) as jfile:
        schwab_data = json.load(jfile)
    sales = [d for d in schwab_data["Transactions"] if d["Action"] == "Sale"]
    transactions_details = []
    date_schwab_format = "%m/%d/%Y"
    for sale in sales:
        sale_date = datetime.strptime(sale["Date"], date_schwab_format)
        if sale_date.year != year:
            continue
        # We will check at the end that the sum of the shares in the transactions is equal to the quantity in the sale event
        # same for the total sale amount
        sale_quantity = int(sale["Quantity"])
        sale_amount_usd = convert_schwab_float_format(sale["Amount"])
        fees_usd = convert_schwab_float_format(sale["FeesAndCommissions"])
        transactions_in_sale = []
        for transaction_dict in sale["TransactionDetails"]:
            transaction_dict = transaction_dict["Details"]
            transaction = TransactionDetails(
                num_shares=int(transaction_dict["Shares"]),
                sale_date=sale_date,
                sale_price_usd=convert_schwab_float_format(
                    transaction_dict["SalePrice"]
                ),
                vest_date=datetime.strptime(
                    transaction_dict["VestDate"], date_schwab_format
                ),
                vest_price_usd=convert_schwab_float_format(
                    transaction_dict["VestFairMarketValue"]
                ),
            )
            transactions_in_sale.append(transaction)

        # Check that we have the same number of shares as expected in the sale event and same total amount
        total_num_shares = sum(tr.num_shares for tr in transactions_in_sale)
        assert total_num_shares == sale_quantity
        total_sale_amount_usd = (
            sum(tr.num_shares * tr.sale_price_usd for tr in transactions_in_sale)
            - fees_usd
        )
        assert abs(total_sale_amount_usd - sale_amount_usd) < 0.01

        transactions_details.extend(transactions_in_sale)
    return transactions_details


def group_transactions(
    transactions: List[TransactionDetails],
) -> List[TransactionDetails]:
    """
    The Schwab JSON file contains multiple transactions for the same (vesting date, sell date).
    They are grouped together in this function.

    Args:
        transactions (List[TransactionDetails]): A list of transactions.

    Returns:
        List[TransactionDetails]: A list of transactions grouped by vesting date.
    """
    grouped_transactions = defaultdict(list)

    for transaction in transactions:
        # Group by vest date and sale date, but also by sale/vest prices (in case of multiple transactions on the same day with different prices)
        # We multiply by 1000 to avoid floating point errors when hashing the key
        ksale_price = int(round(transaction.sale_price_usd * 1000))
        kvest_price = int(round(transaction.vest_price_usd * 1000))
        key = (transaction.vest_date, transaction.sale_date, ksale_price, kvest_price)
        grouped_transactions[key].append(transaction)

    # Now, reduce each group
    reduced_transactions = []
    for group in grouped_transactions.values():
        if len(group) > 1:
            assert all(
                abs(tr.vest_price_usd - group[0].vest_price_usd) < 0.01 for tr in group
            )
            assert all(
                abs(tr.sale_price_usd - group[0].sale_price_usd) < 0.01 for tr in group
            )
            transaction = TransactionDetails(
                num_shares=sum(tr.num_shares for tr in group),
                vest_date=group[0].vest_date,
                vest_price_usd=group[0].vest_price_usd,
                sale_date=group[0].sale_date,
                sale_price_usd=group[0].sale_price_usd,
            )
            reduced_transactions.append(transaction)
        else:
            reduced_transactions.append(group[0])

    return reduced_transactions


def process_transaction(
    src: TransactionDetails, change_data: ExchangeRateData
) -> TransactionDetailsProcessed:
    """
    Process a transaction and calculate various details related to the transaction.

    Args:
        src (TransactionDetails): The transaction details.
        exchange_rate_csv (str): The path to the CSV file containing exchange rate data.

    Returns:
        TransactionDetailsProcessed: The processed transaction details.

    Raises:
        FileNotFoundError: If the exchange rate CSV file is not found.
    """
    minimum_detention_50p = timedelta(days=2 * 365)
    minimum_detention_65p = timedelta(days=8 * 365)
    vest_exchange_rate = change_data.get_euro_dollar_rate(src.vest_date)
    sale_exchange_rate = change_data.get_euro_dollar_rate(src.sale_date)
    vest_price_eur = src.vest_price_usd / vest_exchange_rate
    sale_price_eur = src.sale_price_usd / sale_exchange_rate
    capital_gain_eur = sale_price_eur - vest_price_eur
    total_vest_gain_eur = src.num_shares * vest_price_eur
    total_capital_gain_eur = src.num_shares * capital_gain_eur
    total_sale_price_eur = src.num_shares * sale_price_eur

    # Remove capital losses from acquisition gains
    if total_capital_gain_eur < 0:
        if total_vest_gain_eur >= abs(total_capital_gain_eur):
            total_corrected_vest_gain_eur = total_capital_gain_eur + total_vest_gain_eur
            total_corrected_capital_gain_eur = 0
        else:
            # We cant subtract more that the total acquisition gain, so we set it to 0
            # and still report a loss in the capital gain
            total_corrected_vest_gain_eur = 0
            total_corrected_capital_gain_eur = (
                total_vest_gain_eur + total_capital_gain_eur
            )
    else:
        total_corrected_vest_gain_eur = total_vest_gain_eur
        total_corrected_capital_gain_eur = total_capital_gain_eur

    # Check minimum detention periods for tax relief
    detention = src.sale_date - src.vest_date
    eligible_for_tax_relief_50p = detention > minimum_detention_50p
    eligible_for_tax_relief_65p = detention > minimum_detention_65p

    # Calculate tax relief amount
    if eligible_for_tax_relief_65p:
        tax_relief = 0.65 * total_corrected_vest_gain_eur
    elif eligible_for_tax_relief_50p:
        tax_relief = 0.5 * total_corrected_vest_gain_eur
    else:
        tax_relief = 0

    return TransactionDetailsProcessed(
        num_shares=src.num_shares,
        vest_date=src.vest_date,
        vest_price_usd=src.vest_price_usd,
        sale_date=src.sale_date,
        sale_price_usd=src.sale_price_usd,
        vest_exchange_rate=vest_exchange_rate,
        vest_price_eur=vest_price_eur,
        sale_exchange_rate=sale_exchange_rate,
        sale_price_eur=sale_price_eur,
        capital_gain_eur=capital_gain_eur,
        total_vest_gain_eur=total_vest_gain_eur,
        total_capital_gain_eur=total_capital_gain_eur,
        total_sale_price_eur=total_sale_price_eur,
        detention=detention,
        eligible_for_tax_relief_50p=eligible_for_tax_relief_50p,
        eligible_for_tax_relief_65p=eligible_for_tax_relief_65p,
        taxe_relief_eur=tax_relief,
        total_corrected_vest_gain_eur=total_corrected_vest_gain_eur,
        total_corrected_capital_gain_eur=total_corrected_capital_gain_eur,
    )


def process_all_transactions(transactions: list, change_data: ExchangeRateData) -> list:
    """
    Process a list of transactions and calculate various details for each transaction.

    Args:
        transactions (list): A list of TransactionDetails objects.
        change_data (ExchangeRateData): The exchange rate data.

    Returns:
        list: A list of TransactionDetailsProcessed objects containing the processed transaction details.
    """
    return [process_transaction(tr, change_data) for tr in transactions]


def generate_summary(trs: TransactionDetailsProcessed, mtr: float) -> TaxSummary:
    """
    Summarize the processed transactions.

    Args:
        trs (list): A list of TransactionDetailsProcessed objects.
        mtr (float): Marginal tax rate

    Returns:
        dict: A dictionary containing the summary of the transactions.
    """
    total_vest_gain_eur = sum(tr.total_vest_gain_eur for tr in trs)
    total_capital_gain_eur = sum(tr.total_capital_gain_eur for tr in trs)
    total_sale_price_eur = sum(tr.total_sale_price_eur for tr in trs)
    total_tax_relief_eur = sum(tr.taxe_relief_eur for tr in trs)
    total_corrected_vest_gain_eur = sum(tr.total_corrected_vest_gain_eur for tr in trs)
    total_corrected_capital_gain_eur = sum(
        tr.total_corrected_capital_gain_eur for tr in trs
    )

    # Compute taxes
    # Taxes on acquisition gain
    # We are in the Macron 1 regime, we don't need to distinguish between above or below 300K for the acquisition gain.
    # it's 17.2% of social contribution on the full acquisition gain
    # and MTR (Marginal tax Rate) on the acquistion gain minus the tax relief
    social_contributions_on_vest_gain = 17.2 / 100 * total_corrected_vest_gain_eur
    tax_on_vest_gain = mtr * (total_corrected_vest_gain_eur - total_tax_relief_eur)

    # Tax on capital gain, it's the 30% flat tax
    tax_on_capital_gain = 30 / 100 * total_corrected_capital_gain_eur

    total_taxes = (
        social_contributions_on_vest_gain + tax_on_vest_gain + tax_on_capital_gain
    )
    total_tax_rate = total_taxes / total_sale_price_eur

    return TaxSummary(
        total_vest_gain_eur=total_vest_gain_eur,
        total_capital_gain_eur=total_capital_gain_eur,
        total_sale_price_eur=total_sale_price_eur,
        total_tax_relief_eur=total_tax_relief_eur,
        total_corrected_vest_gain_eur=total_corrected_vest_gain_eur,
        total_corrected_capital_gain_eur=total_corrected_capital_gain_eur,
        social_contributions_on_vest_gain=social_contributions_on_vest_gain,
        tax_on_vest_gain=tax_on_vest_gain,
        tax_on_capital_gain=tax_on_capital_gain,
        total_tax=total_taxes,
        total_tax_rate=total_tax_rate,
    )


def write_output_csv(trs: List[TransactionDetailsProcessed], csv_filename: Path):
    trs.sort(key=lambda x: (x.sale_date, x.vest_date))
    df = pd.DataFrame(trs)
    # Define the mapping between new names and old names
    column_mapping = {
        "num_shares": "Nombre de parts",
        "vest_date": "Date d'acquisition",
        "vest_price_usd": "Prix d'acquisition (USD)",
        "sale_date": "Date de vente",
        "sale_price_usd": "Prix de vente unitaire (USD)",
        "vest_exchange_rate": "Taux de change EUR USD lors de l'acquisition",
        "vest_price_eur": "Prix d'acquisition (EUR)",
        "sale_exchange_rate": "Taux de change EUR USD lors de la vente",
        "sale_price_eur": "Prix de vente unitaire (EUR)",
        "capital_gain_eur": "Plus-value de cession unitaire (EUR)",
        "total_vest_gain_eur": "Gain d'acquisition (EUR)",
        "total_capital_gain_eur": "Plus-value de cession (EUR)",
        "total_sale_price_eur": "Prix de vente (EUR)",
        "detention": "Duree de detention (jours)",
        "eligible_for_tax_relief_50p": "Eligible abattement pour duree de detention entre 2 ans et 8 ans",
        "eligible_for_tax_relief_65p": "Eligible abattement pour duree de detention superieure a 8 ans",
        "taxe_relief_eur": "Abattement (EUR)",
        "total_corrected_vest_gain_eur": "Gain d'acquisition apres imputation des moins-values de cession (EUR)",
        "total_corrected_capital_gain_eur": "Plus-value de cession apres imputation des moins-values de cession (EUR)",
    }
    # Rename the columns using the mapping
    df = df.rename(columns=column_mapping)
    # Use this format so that Google Sheets can parse the number correctly
    df.to_csv(csv_filename, sep="\t", float_format="%.4f", decimal=",")


def write_tax_estimate(summary: TaxSummary, txt_filename: Path):
    s = f"""
    Montant total de la vente: {summary.total_sale_price_eur:.2f} EUR
    Montant total des impots: {summary.total_tax:.2f} EUR
    Taux d'imposition moyen: {summary.total_tax_rate*100:.2f}%
    
    Details:
    - Gain d'acquisition total: {summary.total_corrected_vest_gain_eur:.2f} EUR
        - Contributions sociales sur le gain d'acquisition: {summary.social_contributions_on_vest_gain:.2f} EUR
        - Impots sur le gain d'acquisition: {summary.tax_on_vest_gain:.2f} EUR
    - Plus-value de cession total: {summary.total_corrected_capital_gain_eur:.2f} EUR
        - Impots sur la plus-value de cession: {summary.tax_on_capital_gain:.2f} EUR
    """
    with open(txt_filename, "w") as f:
        f.write(s)


def write_instructions(
    summary: TaxSummary, trs: List[TransactionDetailsProcessed], txt_filename: Path
):
    # TODO(lowik) Transaction with a remaining capital loss should be declared as well (or the capital loss should be subtracted from the total acquisition gain)

    s = f"""
    Instructions:
    - Remplir le formulaire 2042 C
       Case 1TZ: {summary.total_corrected_vest_gain_eur - summary.total_tax_relief_eur:.0f} EUR (Gain d'acquisition apres abattement)
       Case 1UZ: {summary.total_tax_relief_eur:.0f} EUR (Abattement pour duree de detention)
       Case 3VG: {summary.total_corrected_capital_gain_eur:.0f} EUR (Plus-value de cession)
       Attention, le montant de la case 3VG sera peut etre a modifier, apres remplissage du formulaire 2074.
    """

    trs_to_declare = [tr for tr in trs if tr.total_corrected_capital_gain_eur > 0.1]
    trs_to_declare.sort(key=lambda x: (x.sale_date, x.vest_date))

    if not trs_to_declare:
        s += f"""
        Aucune transaction n'a de plus-value de cession a declarer.
        Vous n'avez pas besoin de remplir le formulaire 2074, ni le formulaire 2047.
        """
        with open(txt_filename, "w") as f:
            f.write(s)
        return

    s += f"""
    - Remplir le formulaire 2074
        Nombre de transactions a declarer: {len(trs_to_declare)}
        
    """

    for i, tr in enumerate(trs_to_declare):
        s += f"""
        -------------------------------------------------------------------
        Titre {i+1:02d}:
        - 511 (Designation): META Platforms Inc.
        - 512 (Date cession): {tr.sale_date.strftime("%d/%m/%Y")}
        - 514 (Valeur de cession unitaire): {tr.sale_price_eur:.2f} EUR
        - 515 (Quantite): {tr.num_shares}
        - 516 (Valeur totale): {tr.total_sale_price_eur:.0f} EUR
        - 517 (Frais): Laisser vide
        - 518 (Valeur nette): {tr.total_sale_price_eur:.0f} EUR
        - 520 (Valeur d'acquisition unitaire): {tr.vest_price_eur:.2f} EUR
        - 521 (Prix d'acquisition global): {tr.total_corrected_vest_gain_eur:.0f} EUR
        - 522 (Frais): Laisser vide
        - 523 (Prix de revient): {tr.total_corrected_vest_gain_eur:.0f} EUR
        - 524 (Plus-value de cession): +{tr.total_corrected_capital_gain_eur:.0f} EUR
        -------------------------------------------------------------------
        """

    s += f"""
        Notez la plus value totale obtenue.
    
        1133: Titre A / Colonne A : Recopiez la valeur obtenue, pour qu'elle soit reportee en case 3VG.
        Si la valeur est differente de celle de la case 3VG, retournez au formulaire 2042 C pour ajuster la case 3VG si besoin.
    """

    s += f"""
    - Remplir le formulaire 2047
        - Plus value avant abattement: Etats-Unis - {summary.total_corrected_capital_gain_eur:.0f} EUR (pareil que 3VG)
    """

    with open(txt_filename, "w") as f:
        f.write(s)


from typing import Optional


@click.command()
@click.option(
    "--schwab_json",
    type=click.Path(exists=True, path_type=Path),
    help="Input JSON file containing the Schwab RSU data",
)
@click.option("--year", type=int, help="Year to process the data for")
@click.option(
    "--output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Output directory path",
)
@click.option(
    "--eur_xr_csv",
    default=None,
    type=click.Path(path_type=Path),
    help="CSV file containing the EUR to USD exchange rate data. Will be downloaded if not provided.",
)
@click.option("--mtr", type=float, default=0.41, help="Marginal tax rate")
def main(
    schwab_json: Path,
    year: int,
    output_dir: Path,
    eur_xr_csv: Optional[Path],
    mtr: float,
):
    xr_data = ExchangeRateData(eur_xr_csv)

    transactions = load_transactions_details(schwab_json, year)
    transactions = group_transactions(transactions)
    processed = process_all_transactions(transactions, xr_data)
    summary = generate_summary(processed, mtr)

    output_dir.mkdir(exist_ok=True, parents=True)
    write_output_csv(processed, output_dir / f"rsu_{year}.csv")
    write_tax_estimate(summary, output_dir / f"rsu_tax_estimate_{year}")
    write_instructions(summary, processed, output_dir / f"rsu_tax_instructions_{year}")


if __name__ == "__main__":
    main()
