import click
import pandas as pd
from datetime import datetime
from dataclasses import dataclass
from datetime import timedelta
import json
from collections import defaultdict
from typing import List


class ExchangeRateData:
    def __init__(self, exchange_rate_csv: str):
        self.exchange_rate_csv = exchange_rate_csv
        self.usd_change_rate_by_day = {}
        self._load_exchange_rate_data()

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
        date_str = date.strftime("%d/%m/%Y")
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
    corrected_vest_gain_eur: float
    # Corrected total capital gain in EUR, after removing capital losses and applying tax relief
    corrected_capital_gain_eur: float


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
    # Total acquisition gain over all transactions, below 300K EUR
    total_corrected_vest_gain_eur_below300k: float
    # Total acquisition gain over all transactions, above 300K EUR
    total_corrected_vest_gain_eur_above300k: float
    # Total tax relief over all transactions, that is applicable on the acquistion gain below 300k EUR
    total_valid_tax_relief_eur: float

    # Social contributions on the corrected acquisition gain
    social_contributions_on_vest_gain: float
    # Tax on the on the corrected acquistion gain
    tax_on_vest_gain: float
    # Salary contribution on the corrected acquisition gain
    salary_contribution: float
    # Tax on the corrected capital gain
    tax_on_capital_gain: float
    # Total tax to be paid
    total_tax: float
    # Average tax rate over all transactions
    total_tax_rate: float


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
    # TODO(lowik) Could add a check on sale["Quantity"]
    # TODO(lowik) Take into account the fees and commissions?
    transactions_details = []
    date_schwab_format = "%m/%d/%Y"
    for sale in sales:
        sale_date = datetime.strptime(sale["Date"], date_schwab_format)
        if sale_date.year != year:
            continue
        for transaction_dict in sale["TransactionDetails"]:
            transaction_dict = transaction_dict["Details"]
            transaction = TransactionDetails(
                num_shares=int(transaction_dict["Shares"]),
                sale_date=sale_date,
                # SalePrice format is $XXX.XXXX
                sale_price_usd=float(transaction_dict["SalePrice"][1:]),
                vest_date=datetime.strptime(
                    transaction_dict["VestDate"], date_schwab_format
                ),
                # VestFairMarketValue format is $XXX.XXXX
                vest_price_usd=float(transaction_dict["VestFairMarketValue"][1:]),
            )
            transactions_details.append(transaction)
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
        key = (transaction.vest_date, transaction.sale_date)
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
        if total_vest_gain_eur < abs(total_capital_gain_eur):
            corrected_vest_gain_eur = 0
            corrected_capital_gain_eur = total_capital_gain_eur + total_vest_gain_eur
        else:
            corrected_vest_gain_eur = total_vest_gain_eur + total_capital_gain_eur
            corrected_capital_gain_eur = 0
    else:
        corrected_vest_gain_eur = total_vest_gain_eur
        corrected_capital_gain_eur = total_capital_gain_eur

    # Check minimum detention periods for tax relief
    detention = src.sale_date - src.vest_date
    eligible_for_tax_relief_50p = detention > minimum_detention_50p
    eligible_for_tax_relief_65p = detention > minimum_detention_65p

    # Calculate tax relief amount
    if eligible_for_tax_relief_65p:
        tax_relief = 0.65 * corrected_vest_gain_eur
    elif eligible_for_tax_relief_50p:
        tax_relief = 0.5 * corrected_vest_gain_eur
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
        corrected_vest_gain_eur=corrected_vest_gain_eur,
        corrected_capital_gain_eur=corrected_capital_gain_eur,
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
    processed_transactions = []
    for transaction in transactions:
        processed_transaction = process_transaction(transaction, change_data)
        processed_transactions.append(processed_transaction)
    return processed_transactions


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
    total_corrected_vest_gain_eur = sum(tr.corrected_vest_gain_eur for tr in trs)
    total_corrected_capital_gain_eur = sum(tr.corrected_capital_gain_eur for tr in trs)

    # TODO(lowik) Not sure how to deal with the case where the acquisition gain is above 300k
    # and the tax relief is applied only on the first 300k.
    # For now:
    # - compute the average tax relief percentage
    # - split the total acquisition gain between the first 300k and the rest
    # - apply the tax relief percentage on the first 300k
    avg_tax_relief_percentage = total_tax_relief_eur / total_corrected_vest_gain_eur
    threshold = 300000
    if total_corrected_vest_gain_eur > threshold:
        total_corrected_vest_gain_eur_below300k = threshold
        total_corrected_vest_gain_eur_above300k = (
            total_corrected_vest_gain_eur - threshold
        )
        total_valid_tax_relief_eur = threshold * avg_tax_relief_percentage
    else:
        total_corrected_vest_gain_eur_below300k = total_corrected_vest_gain_eur
        total_corrected_vest_gain_eur_above300k = 0
        total_valid_tax_relief_eur = total_tax_relief_eur

    # Compute taxes
    # Taxes on acquisition gain
    # for the part below 300k, it's 17.2% of social contribution on the full acquisition gain
    # and MTR (Marginal tax Rate) on the acquistion gain minus the tax relief
    social_contributions_on_vest_gain = (
        17.2 / 100 * total_corrected_vest_gain_eur_below300k
    )
    tax_on_vest_gain = mtr * (
        total_corrected_vest_gain_eur_below300k - total_valid_tax_relief_eur
    )
    salary_contribution = 0

    # for the part above 300k
    # It's 9.7% of social contributions and MTR on the full acquisition gain (no tax relief)
    # There's also a 10% salary contribution
    social_contributions_on_vest_gain += (
        9.7 / 100 * total_corrected_vest_gain_eur_above300k
    )
    tax_on_vest_gain += mtr * total_corrected_vest_gain_eur_above300k
    salary_contribution += 10 / 100 * total_corrected_vest_gain_eur_above300k

    # Tax on capital gain, it's the 30% flat tax
    tax_on_capital_gain = 30 / 100 * total_corrected_capital_gain_eur

    total_taxes = (
        social_contributions_on_vest_gain
        + tax_on_vest_gain
        + tax_on_capital_gain
        + salary_contribution
    )
    total_tax_rate = total_taxes / total_sale_price_eur

    return TaxSummary(
        total_vest_gain_eur=total_vest_gain_eur,
        total_capital_gain_eur=total_capital_gain_eur,
        total_sale_price_eur=total_sale_price_eur,
        total_tax_relief_eur=total_tax_relief_eur,
        total_corrected_vest_gain_eur=total_corrected_vest_gain_eur,
        total_corrected_capital_gain_eur=total_corrected_capital_gain_eur,
        total_corrected_vest_gain_eur_below300k=total_corrected_vest_gain_eur_below300k,
        total_corrected_vest_gain_eur_above300k=total_corrected_vest_gain_eur_above300k,
        total_valid_tax_relief_eur=total_valid_tax_relief_eur,
        social_contributions_on_vest_gain=social_contributions_on_vest_gain,
        tax_on_vest_gain=tax_on_vest_gain,
        salary_contribution=salary_contribution,
        tax_on_capital_gain=tax_on_capital_gain,
        total_tax=total_taxes,
        total_tax_rate=total_tax_rate,
    )


def write_output_csv(trs: List[TransactionDetailsProcessed], csv_filename: str):
    df = pd.DataFrame(trs)
    df_rounded = df.round(4)
    # Use this format so that Google Sheets can parse the number correctly
    df_rounded.to_csv(csv_filename, sep='\t', float_format='%.2f', decimal=',')


@click.command()
@click.option("--swchab_json", help="Input JSON file containing the Schwab RSU data")
@click.option("--output_dir", help="Output directory path")
@click.option(
    "--eur_change_csv", help="CSV file containing the EUR to USD exchange rate data"
)
@click.option("--year", type=int, help="Year")
def main(input, output, year):
    transactions = load_transactions_details(input, output, year)
    processed = process_all_transactions(transactions)
    summary = generate_summary(processed)
    write_output_csv(processed, output)
    write_tax_estimate(summary, output)
    write_instructions(summary, output)


if __name__ == "__main__":
    main()
