import sys

import pandas as pd
import numpy as np

from pulp import *
from .gene import AbstractGene

class Haplotype:

    def __init__(self, gene: AbstractGene, sample_prefix: str) -> None:
        """
        Create a new haplotype object
        This object is not a subclass, but inherits data from the Gene class
        Conceptually, Gene is a fairly abstract class that has meta information used
        by the subject and haplotype classes. 

        Args:
            gene (Gene): gene.Gene object
            sample_prefix (str): Sample ID 
        """
        self.matched = False
        self.sample_prefix = sample_prefix
        self.genotypes = gene.get_sample_vars(sample_prefix)

        self.translation_table = gene.get_translation_table_copy()
        self.chromosome = gene.chromosome
        self.version = gene.version
        self.reference = gene.reference
    

    def table_matcher(self) -> None:
        """
        Matches variants in the translation table with the subject's variants
        """
        self.matched = True
        self.translation_table["MATCH"] = self.translation_table.apply(
            lambda x: self._match(x, self.genotypes),
            axis = 1
        )
        self.translation_table["VAR_ID"] = self.translation_table.apply(
                lambda x: f'{x["ID"]}_{x.iloc[7]}',
                axis = 1
                )
        self.translation_table = self.translation_table.drop(self.translation_table.index[self.translation_table["MATCH"] == 99].tolist())
        no_match = self.translation_table[self.translation_table["MATCH"] == 0].iloc[:,0].unique() # Haplotypes where there is any variant not matching
        self.translation_table = self.translation_table[~self.translation_table.iloc[:,0].isin(no_match)] # Drop haplotypes that don't match 100%
        self.variants = self.translation_table.loc[:,["VAR_ID", "MATCH"]].drop_duplicates() # List of matched variants
        self.haplotypes = [hap for hap in self.translation_table.iloc[:,0].unique().tolist()] # List of possible haplotypes

    def _mod_vcf_record(self, alt: str, ref: str) -> str:
        """
        Modifies a subject's variant record to match formatting in the translation table

        Args:
            alt (str): Subject haploid genotype (one allele at a given position)
            ref (str): Reference allele for a position (from VCF)

        Returns:
            str: modified allele
        """
        # if its a del, needs to return -s
        # if its an ins, needs to return just what is inserted
        if alt is None:
            return "-"
        elif len(ref) > len(alt):
            return "id-"
        elif len(ref) > 1:
            return f'id{alt[1:]}' # Remove first position
        else:
            return f's{alt}'
    
    def _mod_tt_record(self, var_type: str, alt: str) -> str:
        """
        Modifies translation table allele to common format

        Args:
            var_type (str): substitution, insertion, deletion
            alt (str): alt allele

        Returns:
            str: modified allele
        """
        if var_type == "insertion":
            return f'id{alt}'
        elif var_type == "deletion":
            return f'id-'
        else:
            return f's{alt}'

    def _match(self, row: pd.core.series.Series, genotypes: [str]) -> int:
        """
        Evaluate match in a single translation table row with a sample

        Args:
            row (pd.core.series.Series): single row from translation table
            genotypes ([type]): list of genotypes

        Returns:
            int: 99 (missing), 0, 1, or 2 (corresponds to the number of matched alleles for a particular position)
        """
        if row.iloc[8] in ["insertion", "deletion"]:
            new_pos = int(row["ID"].split("_")[1]) - 1
            ID = f'{row["ID"].split("_")[0]}_{new_pos}'
        else:
            ID = row["ID"]
        try:
            genotype = genotypes[ID]
        except KeyError:
            return 99
        geno = [self._mod_vcf_record(g, genotype["ref"]) for g in genotype["alleles"]]
        if geno == ["-", "-"]:
            return 99
        tt_alt = self._mod_tt_record(row.iloc[8], row.iloc[7])
        alt_matches = geno.count(tt_alt)
        return(alt_matches)
    
    def optimize_hap(self) -> ():
        """
        Solve for the most likely diplotype

        Returns:
            (): Results
        """
        if not self.matched:
            print("You need to run the table_matcher function with genotyped before you can optimize")
            sys.exit(1)

        num_vars = self.variants.shape[0]
        num_haps = len(self.haplotypes)

        hap_vars = []

        for hap in self.haplotypes:
            trans = self.translation_table[self.translation_table.iloc[:,0] == hap]
            hap_vars.append([1 if var in trans["VAR_ID"].unique() else 0 for var in self.variants["VAR_ID"]])

        hap_prob = LpProblem("Haplotype Optimization", LpMaximize)
        
        # Define the haplotypes variable
        haplotypes = [LpVariable(hap, cat = "LpInteger", lowBound=0, upBound=2) for hap in self.haplotypes]
        variants = [LpVariable(var, cat = "Binary") for var in self.variants["VAR_ID"]]
        

        # Set constraint of two haplotypes selected
        hap_prob += (lpSum(haplotypes[i] for i in range(num_haps)) <= 2) # Cannot choose more than two haplotypes

        # Limit alleles that can be chosen based on zygosity
        for i in range(num_vars): # Iterate over every variant
            # A variant can only be used once per haplotype
            hap_prob += (variants[i] <= (lpSum(hap_vars[k][i] * haplotypes[k] for k in range(num_haps))))
            # A given variant cannot be used more than "MATCH"
            hap_prob += ((lpSum(hap_vars[k][i] * haplotypes[k] for k in range(num_haps))) <= self.variants.iloc[i,1] * variants[i])

        # Set to maximize the number of variant alleles used
        hap_prob += lpSum(
            self.translation_table[
                self.translation_table.iloc[:,0] == self.haplotypes[i]
                ]["MATCH"].sum() * haplotypes[i] for i in range(num_haps))

        hap_prob.solve()
        haps = []
        variants = []

        for v in hap_prob.variables():
            if v.varValue:
                if v.varValue > 0:
                    if v.name.split("_")[0] == f'c{self.chromosome}':
                        variants.append(v.name)
                    else:
                        haps.append((v.name, v.varValue))
        if len(haps) == 0:
            called = [self.reference, self.reference]
        elif len(haps) == 2:
            called = [haps[0][0], haps[1][0]]
        else:
            called = np.array([np.repeat(i[0], i[1]) for i in haps]).flatten().tolist()
            if len(called) == 1:
                called.append(self.reference)
        return self.version, called, variants
        